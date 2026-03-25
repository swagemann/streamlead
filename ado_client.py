# ado_client.py
import re
import pandas as pd
from azure.devops.connection import Connection
from azure.identity import InteractiveBrowserCredential
from msrest.authentication import BasicTokenAuthentication
from azure.devops.v7_0.work_item_tracking.models import Wiql
from azure.devops.v7_0.work_item_tracking.models import TeamContext
from azure.devops.v7_0.git.models import GitQueryCommitsCriteria, GitPullRequestSearchCriteria

FIELDS = [
    "System.Id",
    "System.State",
    "System.AssignedTo",
    "System.WorkItemType",
    "System.CreatedDate",
    "Microsoft.VSTS.Common.ClosedDate",
    "System.AreaPath",
    "System.Title",
    "System.Tags",
    "System.CommentCount",
    "System.BoardLane",
]

BATCH_SIZE = 200
FTCASE_PATTERN = re.compile(r"FTCASE#\d+#")

ADO_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"


def get_credential():
    """Create an interactive browser credential for Azure AD login."""
    return InteractiveBrowserCredential()


def get_ado_connection(org_url, token):
    """Create Azure DevOps connection using a bearer token."""
    credentials = BasicTokenAuthentication({"access_token": token})
    return Connection(base_url=org_url, creds=credentials)


def fetch_work_items(connection, project, wiql_query):
    client = connection.clients.get_work_item_tracking_client()
    team_context = TeamContext(project=project)
    result = client.query_by_wiql(Wiql(query=wiql_query), team_context=team_context)

    ids = [ref.id for ref in result.work_items]
    if not ids:
        return pd.DataFrame(
            columns=[
                "id", "state", "assigned_to", "type",
                "created_date", "closed_date",
                "area_path", "title", "tags", "comment_count",
                "board_lane",
            ]
        )

    all_work_items = []
    for i in range(0, len(ids), BATCH_SIZE):
        batch = ids[i : i + BATCH_SIZE]
        all_work_items.extend(client.get_work_items(ids=batch, fields=FIELDS))

    rows = []
    for wi in all_work_items:
        f = wi.fields
        assigned = f.get("System.AssignedTo")
        assigned_name = assigned.get("displayName") if isinstance(assigned, dict) else None
        title = f.get("System.Title", "")
        raw_tags = f.get("System.Tags", "") or ""

        # Append "Fleet Track" tag if title matches FTCASE pattern
        if FTCASE_PATTERN.search(title):
            if raw_tags:
                tag_parts = [t.strip() for t in raw_tags.split(";") if t.strip()]
            else:
                tag_parts = []
            if "Fleet Track" not in tag_parts:
                tag_parts.append("Fleet Track")
            raw_tags = "; ".join(tag_parts)

        rows.append(
            {
                "id": wi.id,
                "state": f.get("System.State"),
                "assigned_to": assigned_name,
                "type": f.get("System.WorkItemType"),
                "created_date": pd.to_datetime(f.get("System.CreatedDate")),
                "closed_date": pd.to_datetime(f.get("Microsoft.VSTS.Common.ClosedDate")),
                "area_path": f.get("System.AreaPath", ""),
                "title": title,
                "tags": raw_tags,
                "comment_count": f.get("System.CommentCount", 0) or 0,
                "board_lane": f.get("System.BoardLane") or "",
            }
        )

    return pd.DataFrame(rows)


def fetch_repos(connection, project, repo_names):
    """Fetch repository objects by name. Returns dict of name -> repo."""
    try:
        git_client = connection.clients.get_git_client()
        all_repos = git_client.get_repositories(project)
        name_set = set(r.lower() for r in repo_names)
        return {r.name: r for r in all_repos if r.name.lower() in name_set}
    except Exception:
        return {}


def fetch_git_commits(connection, project, repo_id, team_members, from_date, to_date):
    """Fetch commits from a repo filtered by team members and date range."""
    try:
        git_client = connection.clients.get_git_client()
        criteria = GitQueryCommitsCriteria(
            from_date=from_date,
            to_date=to_date,
        )
        commits = git_client.get_commits(
            repository_id=repo_id,
            project=project,
            search_criteria=criteria,
            top=1000,
        )
        if not commits:
            return pd.DataFrame(columns=["repo", "commit_id", "author", "date", "message"])

        member_set = set(m.lower() for m in team_members) if team_members else None
        rows = []
        for c in commits:
            author_name = c.author.name if c.author else None
            if member_set and (not author_name or author_name.lower() not in member_set):
                continue
            rows.append({
                "commit_id": c.commit_id[:8] if c.commit_id else "",
                "author": author_name or "Unknown",
                "date": pd.to_datetime(c.author.date) if c.author and c.author.date else None,
                "message": (c.comment or "").split("\n")[0][:120],
            })
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame(columns=["commit_id", "author", "date", "message"])


def fetch_pull_requests(connection, project, repo_id, team_members, from_date=None):
    """Fetch pull requests from a repo."""
    try:
        git_client = connection.clients.get_git_client()
        criteria = GitPullRequestSearchCriteria(status="all")
        prs = git_client.get_pull_requests(
            repository_id=repo_id,
            project=project,
            search_criteria=criteria,
            top=200,
        )
        if not prs:
            return pd.DataFrame(columns=["pr_id", "title", "author", "status", "created", "closed", "reviewers"])

        member_set = set(m.lower() for m in team_members) if team_members else None
        from_dt = pd.to_datetime(from_date) if from_date else None
        rows = []
        for pr in prs:
            created = pd.to_datetime(pr.creation_date) if pr.creation_date else None
            if from_dt and created and created < from_dt:
                continue

            author_name = pr.created_by.display_name if pr.created_by else None
            if member_set and (not author_name or author_name.lower() not in member_set):
                continue

            status_map = {1: "Active", 2: "Abandoned", 3: "Completed"}
            status = status_map.get(pr.status, str(pr.status)) if isinstance(pr.status, int) else str(pr.status or "")

            reviewers = ", ".join(
                r.display_name for r in (pr.reviewers or []) if r.display_name
            )

            rows.append({
                "pr_id": pr.pull_request_id,
                "title": pr.title or "",
                "author": author_name or "Unknown",
                "status": status,
                "created": created,
                "closed": pd.to_datetime(pr.closed_date) if pr.closed_date else None,
                "reviewers": reviewers,
            })
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame(columns=["pr_id", "title", "author", "status", "created", "closed", "reviewers"])


def fetch_builds(connection, project, from_date=None):
    """Fetch build/pipeline runs."""
    try:
        build_client = connection.clients.get_build_client()
        builds = build_client.get_builds(
            project=project,
            min_time=from_date,
            top=200,
        )
        if not builds:
            return pd.DataFrame(columns=["build_id", "pipeline", "status", "result", "requested_by", "start_time", "finish_time", "branch"])

        rows = []
        for b in builds:
            rows.append({
                "build_id": b.id,
                "pipeline": b.definition.name if b.definition else "",
                "status": str(b.status or ""),
                "result": str(b.result or ""),
                "requested_by": b.requested_by.display_name if b.requested_by else "",
                "start_time": pd.to_datetime(b.start_time) if b.start_time else None,
                "finish_time": pd.to_datetime(b.finish_time) if b.finish_time else None,
                "branch": (b.source_branch or "").replace("refs/heads/", ""),
            })
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame(columns=["build_id", "pipeline", "status", "result", "requested_by", "start_time", "finish_time", "branch"])


def fetch_last_team_comment_dates(connection, project, work_item_ids, team_members):
    """Fetch the date of the last comment by a team member for each work item."""
    client = connection.clients.get_work_item_tracking_client()
    team_set = set(team_members)
    result = {}
    for wid in work_item_ids:
        try:
            comments = client.get_comments(project, wid)
            last_date = None
            if comments.comments:
                for c in comments.comments:
                    author_name = c.created_by.display_name if c.created_by else None
                    if author_name in team_set:
                        cdate = pd.to_datetime(c.created_date)
                        if last_date is None or cdate > last_date:
                            last_date = cdate
            result[wid] = last_date
        except Exception:
            result[wid] = None
    return result
