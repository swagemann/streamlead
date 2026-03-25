# ado_client.py
import re
import pandas as pd
from azure.devops.connection import Connection
from azure.identity import InteractiveBrowserCredential
from msrest.authentication import BasicTokenAuthentication
from azure.devops.v7_0.work_item_tracking.models import Wiql
from azure.devops.v7_0.work_item_tracking.models import TeamContext

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
