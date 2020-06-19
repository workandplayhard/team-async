from pprint import pprint
from flask import Flask
from githubapp import GitHubApp, LDAPClient
from distutils.util import strtobool
import os

app = Flask(__name__)
github_app = GitHubApp(app)
ldap = LDAPClient()

@github_app.on('team.created')
def sync_team():
    """

    :return:
    """
    # pprint(github_app.payload)
    payload = github_app.payload
    owner = github_app.payload['organization']['login']
    org = github_app.installation_client.organization(owner)
    team = org.team(payload['team']['id'])
    slug = payload['team']['slug']
    parent = payload['team']['parent']
    ldap_members = ldap_lookup(group=slug)
    team_members = github_lookup(
        team_id=payload['team']['id'],
        attribute='username'
    )

    compare = compare_members(
        ldap_group=ldap_members,
        github_team=team_members,
        attribute='username'
    )
    pprint(compare)
    try:
        execute_sync(
            org=org,
            team=team,
            slug=slug,
            state=compare
        )
    except ValueError as e:
        if strtobool(os.environ['OPEN_ISSUE_ON_FAILURE']):
            open_issue(slug=slug, message=e)
    except AssertionError as e:
        if strtobool(os.environ['OPEN_ISSUE_ON_FAILURE']):
            open_issue(slug=slug, message=e)


def ldap_lookup(group=None):
    """
    Look up members of a group in LDAP
    :param group: The name of the group to query in LDAP
    :type group: str
    :return: ldap_members
    :rtype: list
    """
    group_members = ldap.get_group_members(group)
    ldap_members = [member for member in group_members]
    return ldap_members


def github_team(team_id):
    """
    Look up team info in GitHub
    :param team_id:
    :return:
    """
    owner = github_app.payload['organization']['login']
    org = github_app.installation_client.organization(owner)
    return org.team(team_id)


def github_lookup(team_id=None, attribute='username'):
    """
    Look up members of a give team in GitHub
    :param team_id:
    :param attribute:
    :type team_id: int
    :type attribute: str
    :return: team_members
    :rtype: list
    """
    team_members = []
    team = github_team(team_id)
    if attribute == 'email':
        for m in team.members():
            user = github_app.installation_client.user(m.login)
            team_members.append({'username': str(user.login).casefold(),
                                 'email': str(user.email).casefold()})
    else:
        for member in team.members():
            team_members.append({'username': str(member).casefold(),
                                 'email': ''})
    return team_members


def compare_members(ldap_group, github_team, attribute='username'):
    """
    Compare users in GitHub and LDAP to see which users need to be added or removed
    :param ldap_group:
    :param github_team:
    :param attribute:
    :return: sync_state
    :rtype: dict
    """
    ldap_list = [x[attribute] for x in ldap_group]
    github_list = [x[attribute] for x in github_team]
    add_users = list(set(ldap_list) - set(github_list))
    remove_users = list(set(github_list) - set(ldap_list))
    sync_state = {
        'ldap': ldap_group,
        'github': github_team,
        'action': {
            'add': add_users,
            'remove': remove_users
        }
    }
    return sync_state


def execute_sync(org, team, slug, state):
    """
    Perform the synchronization
    :param org:
    :param team:
    :param slug:
    :param state:
    :return:
    """
    total_changes = len(state['action']['remove']) + len(state['action']['add'])
    if len(state['ldap']) == 0:
        message = "LDAP group returned empty: {}".format(slug)
        raise ValueError(message)
    elif int(total_changes) > int(os.environ['CHANGE_THRESHOLD']):
        message = "Skipping sync for {}.<br>".format(slug)
        message += "Total number of changes ({}) would exceed the change threshold ({}).".format(
            str(total_changes), str(os.environ['CHANGE_THRESHOLD'])
        )
        message += "<br>Please investigate this change and increase your threshold if this is accurate."
        raise AssertionError(message)
    else:
        for user in state['action']['add']:
            # Validate that user is in org
            if org.is_member(user):
                pprint(f'Adding {user} to {slug}')
                team.add_or_update_membership(user)
            else:
                pprint(f'Skipping {user} as they are not part of the org')

        for user in state['action']['remove']:
            pprint(f'Removing {user} from {slug}')
            team.revoke_membership(user)

def open_issue(slug, message):
    repo_for_issues = os.environ['REPO_FOR_ISSUES']
    owner = repo_for_issues.split('/')[0]
    repository = repo_for_issues.split('/')[1]
    assignee = os.environ['ISSUE_ASSIGNEE']
    issue = github_app.installation_client.create_issue(
        owner=owner,
        repository=repository,
        assignee=assignee,
        title="Team sync failed for @{}/{}".format(owner, slug),
        body=str(message)
    )
