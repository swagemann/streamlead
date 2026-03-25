# app.py
import streamlit as st
import plotly.express as px
import pandas as pd
import os
from ado_client import (
    get_credential, get_ado_connection, fetch_work_items,
    fetch_last_team_comment_dates, fetch_repos, fetch_git_commits,
    fetch_pull_requests, fetch_builds, ADO_SCOPE,
)
from teams import load_teams

st.set_page_config(page_title="ADO Dashboard", layout="wide")
# st.title("Data Modeling Management Dashboard")

# --- Session state for auth ---
if "credential" not in st.session_state:
    st.session_state.credential = None

teams = load_teams()
team_names = list(teams.keys())

# --- Sidebar: Config ---
with st.sidebar:
    st.header("Configuration")
    org_url = st.text_input("Org URL", value="https://dev.azure.com/HOLMAN")
    project = st.text_input("Project", value="IT")

    if st.session_state.credential is None:
        if st.button("Sign In with Microsoft"):
            try:
                cred = get_credential()
                cred.get_token(ADO_SCOPE)
                st.session_state.credential = cred
                st.rerun()
            except Exception as e:
                st.error(f"Authentication failed: {e}")
    else:
        st.success("Signed in")
        if st.button("Sign Out"):
            st.session_state.credential = None
            st.rerun()

    st.divider()
    st.header("Timeframe")
    date_range = st.date_input("Date Range", value=[
        pd.Timestamp.now() - pd.Timedelta(days=180),
        pd.Timestamp.now()
    ])

    st.divider()
    selected_team = st.selectbox("Team", team_names if team_names else ["(No teams configured)"])

# --- Data Fetch (cached) ---
@st.cache_data(ttl=300)
def load_data(org_url, token, project, start_date, end_date, area_paths, members):
    conn = get_ado_connection(org_url, token)

    clauses = []
    if area_paths:
        area_clause = " OR ".join(
            f"[System.AreaPath] UNDER '{project}\\{ap}'" for ap in area_paths
        )
        clauses.append(f"({area_clause})")
    if members:
        member_clause = " OR ".join(
            f"[System.AssignedTo] = '{m}'" for m in members
        )
        clauses.append(f"({member_clause})")

    if not clauses:
        return pd.DataFrame(columns=[
            "id", "state", "assigned_to", "type",
            "created_date", "closed_date",
            "area_path", "title", "tags", "comment_count",
            "board_lane",
        ])

    combined = " OR ".join(clauses)
    wiql = f"""
        SELECT [System.Id] FROM WorkItems
        WHERE [System.TeamProject] = '{project}'
        AND ({combined})
        AND (
            ([System.CreatedDate] >= '{start_date}' AND [System.CreatedDate] <= '{end_date}')
            OR
            ([Microsoft.VSTS.Common.ClosedDate] >= '{start_date}' AND [Microsoft.VSTS.Common.ClosedDate] <= '{end_date}')
        )
        ORDER BY [System.CreatedDate] DESC
    """
    return fetch_work_items(conn, project, wiql)


@st.cache_data(ttl=300)
def load_comment_dates(org_url, token, project, work_item_ids, team_members):
    conn = get_ado_connection(org_url, token)
    return fetch_last_team_comment_dates(conn, project, work_item_ids, team_members)


@st.cache_data(ttl=300)
def load_git_commits(org_url, token, project, repo_names, members, start_date, end_date):
    conn = get_ado_connection(org_url, token)
    repos = fetch_repos(conn, project, list(repo_names))
    if not repos:
        raise ValueError(f"No repos matched names: {list(repo_names)}. Check that the repo names in teams.json match Azure DevOps exactly.")
    all_dfs = []
    for name, repo in repos.items():
        df = fetch_git_commits(conn, project, repo.id, list(members), start_date, end_date)
        if not df.empty:
            df["repo"] = name
            all_dfs.append(df)
    return pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame(columns=["repo", "commit_id", "author", "date", "message"])


@st.cache_data(ttl=300)
def load_pull_requests(org_url, token, project, repo_names, members, start_date):
    conn = get_ado_connection(org_url, token)
    repos = fetch_repos(conn, project, list(repo_names))
    if not repos:
        raise ValueError(f"No repos matched names: {list(repo_names)}. Check that the repo names in teams.json match Azure DevOps exactly.")
    all_dfs = []
    for name, repo in repos.items():
        df = fetch_pull_requests(conn, project, repo.id, list(members), start_date)
        if not df.empty:
            df["repo"] = name
            all_dfs.append(df)
    return pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame(columns=["repo", "pr_id", "title", "author", "status", "created", "closed", "reviewers"])


@st.cache_data(ttl=300)
def load_builds(org_url, token, project, start_date):
    conn = get_ado_connection(org_url, token)
    return fetch_builds(conn, project, start_date)


st.title(f"Management Dashboard: {selected_team}")

tab_dashboard, tab_git, tab_report, tab_docs = st.tabs(["Dashboard", "Git & Deployments", "Summary Report", "Docs"])

with tab_docs:
    docs_path = os.path.join(os.path.dirname(__file__), "ado-ticket-guidelines.md")
    if os.path.exists(docs_path):
        with open(docs_path, "r") as f:
            st.markdown(f.read())
    else:
        st.warning("Documentation file not found.")

with tab_report:
    if st.session_state.credential and project and selected_team and selected_team != "(No teams configured)":
        report_token = st.session_state.credential.get_token(ADO_SCOPE).token
        team_config_r = teams[selected_team]
        team_members_r = team_config_r.get("members", [])
        team_areas_r = team_config_r.get("areas", [])

        report_end = pd.Timestamp.now(tz="UTC")
        report_start = report_end - pd.Timedelta(weeks=3)

        report_df = load_data(
            org_url, report_token, project,
            str(report_start.date()), str(report_end.date()),
            tuple(team_areas_r), tuple(team_members_r)
        )

        if not report_df.empty:
            # Enrich data
            report_df["tags_list"] = report_df["tags"].apply(
                lambda t: [x.strip() for x in t.split(";") if x.strip()] if t else []
            )
            report_df["display_area"] = report_df["area_path"].apply(
                lambda p: p.split("\\")[-1] if p else "Unknown"
            )
            report_df["is_fleettrack"] = report_df["tags_list"].apply(lambda t: "Fleet Track" in t)

            closed_states = ["Closed", "Resolved", "Done", "Complete"]
            closed_df = report_df[
                (report_df["state"].isin(closed_states)) &
                (report_df["closed_date"] >= report_start) &
                (report_df["assigned_to"].isin(team_members_r))
            ]

            # Also gather active/in-progress tickets for context
            active_states = ["Approved", "Active", "In Progress"]
            active_df = report_df[
                (report_df["state"].isin(active_states)) &
                (report_df["assigned_to"].isin(team_members_r))
            ]

            period_label = f"{report_start.strftime('%B %d')} – {report_end.strftime('%B %d, %Y')}"
            st.markdown(f"### {selected_team} — Summary Report Generator")
            st.caption(period_label)
            st.markdown(
                "This tab builds an AI prompt from your team's ticket data. "
                "Copy it into Claude / ChatGPT and get a polished report back instantly."
            )

            # Build per-person report data
            report_persons = []
            for member in sorted(team_members_r):
                m_closed = closed_df[closed_df["assigned_to"] == member]
                m_active = active_df[active_df["assigned_to"] == member]

                if m_closed.empty and m_active.empty:
                    continue

                total_closed = len(m_closed)
                total_comments = int(m_closed["comment_count"].sum()) if not m_closed.empty else 0

                # Group closed: FleetTrack vs other areas
                ft_closed = m_closed[m_closed["is_fleettrack"]] if not m_closed.empty else pd.DataFrame()
                other_closed = m_closed[~m_closed["is_fleettrack"]] if not m_closed.empty else pd.DataFrame()

                closed_groups = {}
                if not ft_closed.empty:
                    closed_groups["FleetTrack"] = ft_closed[["title", "type"]].to_dict("records")
                if not other_closed.empty:
                    for area, grp in other_closed.groupby("display_area"):
                        closed_groups[area] = grp[["title", "type"]].to_dict("records")

                # Group active: FleetTrack vs other areas
                ft_active = m_active[m_active["is_fleettrack"]] if not m_active.empty else pd.DataFrame()
                other_active = m_active[~m_active["is_fleettrack"]] if not m_active.empty else pd.DataFrame()

                active_groups = {}
                if not ft_active.empty:
                    active_groups["FleetTrack"] = ft_active[["title", "type"]].to_dict("records")
                if not other_active.empty:
                    for area, grp in other_active.groupby("display_area"):
                        active_groups[area] = grp[["title", "type"]].to_dict("records")

                report_persons.append({
                    "name": member,
                    "closed": total_closed,
                    "comments": total_comments,
                    "closed_groups": closed_groups,
                    "active_groups": active_groups,
                })

            if report_persons:
                # --- Preview the raw data in the tab ---
                for person in report_persons:
                    st.markdown("---")
                    st.markdown(f"#### {person['name']}")
                    col_a, col_b = st.columns(2)
                    col_a.metric("Tickets Closed", person["closed"])
                    col_b.metric("Total Comments", person["comments"])

                    if person["closed_groups"]:
                        st.markdown("**Completed**")
                        for area_name, tickets in person["closed_groups"].items():
                            st.markdown(f"*{area_name}* — {len(tickets)} ticket{'s' if len(tickets) != 1 else ''}")
                            for t in tickets:
                                st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;• {t['title']}  `{t['type']}`")

                    if person["active_groups"]:
                        st.markdown("**In Progress**")
                        for area_name, tickets in person["active_groups"].items():
                            st.markdown(f"*{area_name}* — {len(tickets)} ticket{'s' if len(tickets) != 1 else ''}")
                            for t in tickets:
                                st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;• {t['title']}  `{t['type']}`")

                # --- Build the AI prompt ---
                def build_ai_prompt(persons, team_name, period):
                    data_block = ""
                    for person in persons:
                        data_block += f"\n### {person['name']}\n"
                        data_block += f"Tickets closed: {person['closed']} | Total comments: {person['comments']}\n"

                        if person["closed_groups"]:
                            data_block += "\nCOMPLETED WORK:\n"
                            for area_name, tickets in person["closed_groups"].items():
                                data_block += f"  [{area_name}]\n"
                                for t in tickets:
                                    data_block += f"    - ({t['type']}) {t['title']}\n"

                        if person["active_groups"]:
                            data_block += "\nCURRENTLY IN PROGRESS:\n"
                            for area_name, tickets in person["active_groups"].items():
                                data_block += f"  [{area_name}]\n"
                                for t in tickets:
                                    data_block += f"    - ({t['type']}) {t['title']}\n"

                    prompt = f"""You are a technical writing assistant. Write a professional work summary report for my manager.

REPORT PARAMETERS:
- Team: {team_name}
- Period: {period}
- Audience: Senior management (non-technical — keep it clear and concise)
- Length: Roughly one page when printed
- Tone: Professional, confident, factual

FORMAT REQUIREMENTS:
- Title: "{team_name} — Work Summary" with the date range below it
- One section per team member with their name as the heading
- Under each person: a short narrative paragraph (2-4 sentences) summarizing what they accomplished and what they're currently working on. Group related tickets into themes rather than listing every ticket individually. Anything tagged FleetTrack should be described under a "FleetTrack" sub-topic.
- After the narrative, include a compact stats line: "X tickets closed | Y comments"
- End with a brief 1-2 sentence team-level summary noting overall throughput and any cross-cutting themes.
- Do NOT use bullet points for the narrative — use flowing prose. Keep it scannable but polished.
- Do NOT fabricate details. Only describe what the ticket titles suggest.

RAW TICKET DATA:
{data_block}

Write the report now."""
                    return prompt

                prompt_text = build_ai_prompt(report_persons, selected_team, period_label)

                st.markdown("---")
                st.markdown("#### AI Prompt")
                st.caption("Copy this prompt and paste it into Claude, ChatGPT, or any AI to generate your polished report.")

                st.code(prompt_text, language=None)

                # Copy-friendly download as .txt
                st.download_button(
                    label="Download Prompt (.txt)",
                    data=prompt_text,
                    file_name=f"{selected_team.lower().replace(' ', '_')}_prompt_{report_end.strftime('%Y%m%d')}.txt",
                    mime="text/plain",
                )
            else:
                st.info("No closed or active tickets found for team members in the last 3 weeks.")
        else:
            st.info("No work items found for the last 3 weeks.")
    else:
        st.info("Sign in with your Microsoft account and select a team to get started.")

with tab_git:
    if st.session_state.credential and project and selected_team and selected_team != "(No teams configured)":
        git_token = st.session_state.credential.get_token(ADO_SCOPE).token
        git_team_config = teams[selected_team]
        git_members = git_team_config.get("members", [])
        git_repos = git_team_config.get("repos", [])
        git_project = git_team_config.get("repo_project", project)

        if not git_repos:
            st.info("No repos configured for this team. Add repo names to the `repos` list in teams.json.")
        else:
            git_start = str(date_range[0])
            git_end = str(date_range[1])

            col_commits, col_prs = st.columns(2)

            # --- Commits ---
            with col_commits:
                st.subheader("Commits")
                try:
                    commits_df = load_git_commits(
                        org_url, git_token, git_project,
                        tuple(git_repos), tuple(git_members),
                        git_start, git_end,
                    )
                    if not commits_df.empty:
                        # Summary metrics
                        c1, c2 = st.columns(2)
                        c1.metric("Total Commits", len(commits_df))
                        c2.metric("Contributors", commits_df["author"].nunique())

                        # Commits by author
                        if len(commits_df) > 0:
                            author_counts = commits_df["author"].value_counts().reset_index()
                            author_counts.columns = ["Author", "Commits"]
                            fig_commits = px.bar(author_counts, x="Author", y="Commits", color="Author")
                            fig_commits.update_layout(showlegend=False)
                            st.plotly_chart(fig_commits, use_container_width=True)

                        # Recent commits table
                        st.markdown("**Recent Commits**")
                        display_commits = commits_df[["repo", "commit_id", "author", "date", "message"]].copy()
                        display_commits["date"] = display_commits["date"].dt.strftime("%Y-%m-%d %H:%M")
                        display_commits.columns = ["Repo", "SHA", "Author", "Date", "Message"]
                        st.dataframe(display_commits.head(50), use_container_width=True, hide_index=True)
                    else:
                        st.info("No commits found for the selected date range and team members.")
                except Exception as e:
                    st.warning(f"Could not load commits: {e}")

            # --- Pull Requests ---
            with col_prs:
                st.subheader("Pull Requests")
                try:
                    prs_df = load_pull_requests(
                        org_url, git_token, git_project,
                        tuple(git_repos), tuple(git_members),
                        git_start,
                    )
                    if not prs_df.empty:
                        # Summary metrics
                        p1, p2, p3 = st.columns(3)
                        p1.metric("Total PRs", len(prs_df))
                        completed = len(prs_df[prs_df["status"] == "Completed"])
                        active = len(prs_df[prs_df["status"] == "Active"])
                        p2.metric("Completed", completed)
                        p3.metric("Active", active)

                        # PRs by author
                        pr_author_counts = prs_df["author"].value_counts().reset_index()
                        pr_author_counts.columns = ["Author", "PRs"]
                        fig_prs = px.bar(pr_author_counts, x="Author", y="PRs", color="Author")
                        fig_prs.update_layout(showlegend=False)
                        st.plotly_chart(fig_prs, use_container_width=True)

                        # PR table
                        st.markdown("**Pull Requests**")
                        display_prs = prs_df[["repo", "pr_id", "title", "author", "status", "created", "reviewers"]].copy()
                        display_prs["created"] = display_prs["created"].dt.strftime("%Y-%m-%d")
                        display_prs.columns = ["Repo", "ID", "Title", "Author", "Status", "Created", "Reviewers"]
                        st.dataframe(display_prs.head(50), use_container_width=True, hide_index=True)
                    else:
                        st.info("No pull requests found for the selected date range and team members.")
                except Exception as e:
                    st.warning(f"Could not load pull requests: {e}")

            # --- Builds / Deployments ---
            st.divider()
            st.subheader("Pipelines / Deployments")
            try:
                builds_df = load_builds(org_url, git_token, git_project, git_start)
                if not builds_df.empty:
                    # Filter to team repos by branch if possible
                    b1, b2, b3 = st.columns(3)
                    b1.metric("Total Runs", len(builds_df))
                    succeeded = len(builds_df[builds_df["result"].str.lower() == "succeeded"])
                    failed = len(builds_df[builds_df["result"].str.lower() == "failed"])
                    b2.metric("Succeeded", succeeded)
                    b3.metric("Failed", failed)

                    display_builds = builds_df[["build_id", "pipeline", "result", "requested_by", "start_time", "branch"]].copy()
                    display_builds["start_time"] = display_builds["start_time"].dt.strftime("%Y-%m-%d %H:%M")
                    display_builds.columns = ["ID", "Pipeline", "Result", "Requested By", "Started", "Branch"]
                    st.dataframe(display_builds.head(50), use_container_width=True, hide_index=True)
                else:
                    st.info("No pipeline runs found for the selected date range.")
            except Exception as e:
                st.warning(f"Could not load pipeline data: {e}")
    else:
        st.info("Sign in with your Microsoft account and select a team to get started.")

with tab_dashboard:
    if st.session_state.credential and project and selected_team and selected_team != "(No teams configured)":
        token = st.session_state.credential.get_token(ADO_SCOPE).token

        team_config = teams[selected_team]
        team_members = team_config.get("members", [])
        team_areas = team_config.get("areas", [])

        df = load_data(
            org_url, token, project,
            str(date_range[0]), str(date_range[1]),
            tuple(team_areas), tuple(team_members)
        )

        # Derive tags_list
        df["tags_list"] = df["tags"].apply(
            lambda t: [x.strip() for x in t.split(";") if x.strip()] if t else []
        )

        # Build team lookup
        member_to_team = {}
        for tn, tc in teams.items():
            for m in tc.get("members", []):
                member_to_team[m] = tn
        df["team"] = df["assigned_to"].map(member_to_team).fillna("Unassigned")

        # Compute ticket age
        now = pd.Timestamp.now(tz="UTC")
        df["age_days"] = (now - df["created_date"]).dt.days

        # Determine if ticket is in designated areas
        def is_in_designated_areas(area_path):
            for ap in team_areas:
                full_path = f"{project}\\{ap}"
                if area_path == full_path or area_path.startswith(full_path + "\\"):
                    return True
            return False

        df["in_designated_area"] = df["area_path"].apply(is_in_designated_areas)

        # Extract display area (last segment or child area)
        df["display_area"] = df["area_path"].apply(lambda p: p.split("\\")[-1] if p else "Unknown")

        # Fetch last team comment dates for open tickets assigned to team
        open_mask = ~df["state"].isin(["Closed", "Resolved", "Done", "Complete"])
        team_mask = df["assigned_to"].isin(team_members)
        open_team_ids = df.loc[open_mask & team_mask, "id"].tolist()

        if open_team_ids:
            comment_dates = load_comment_dates(
                org_url, token, project,
                tuple(sorted(open_team_ids)),
                tuple(sorted(team_members))
            )
            df["last_team_comment"] = df["id"].map(comment_dates)
        else:
            df["last_team_comment"] = pd.NaT

        df["days_since_comment"] = df["last_team_comment"].apply(
            lambda d: (now - d).days if pd.notna(d) else None
        )

        # Main filtered data (in designated areas)
        filtered = df[df["in_designated_area"]]

        # --- KPI Row ---
        closed_items = filtered.dropna(subset=["closed_date"])
        days_to_close = closed_items.apply(
            lambda r: (r["closed_date"] - r["created_date"]).days, axis=1
        )
        median_days = days_to_close.median() if len(days_to_close) > 0 else None

        def count_area(keyword):
            return len(filtered[filtered["area_path"].str.contains(keyword, case=False, na=False)])

        total_tickets = len(filtered)
        active_tickets = len(filtered[filtered["state"].isin(["Approved", "Active", "In Progress", "New", "Created", "Evaluate"])])
        closed_tickets = len(closed_items)

        # Aging KPIs (exclude Backlog swim lane)
        non_closed = filtered[~filtered["state"].isin(["Closed", "Resolved", "Done", "Complete"])]
        open_for_aging = non_closed[non_closed["board_lane"].str.lower() != "backlog"]
        over_2_weeks_count = len(open_for_aging[open_for_aging["age_days"] > 14])
        over_month_count = len(open_for_aging[open_for_aging["age_days"] > 30])

        # Stale KPI (same logic as stale tickets table)
        open_for_stale = open_for_aging[open_for_aging["assigned_to"].isin(team_members)]
        stale_count_kpi = len(open_for_stale[
            (open_for_stale["age_days"] > 7) &
            ((open_for_stale["days_since_comment"].isna()) | (open_for_stale["days_since_comment"] > 7))
        ])

        # Wrong area count
        wrong_area_count = len(df[
            (~df["in_designated_area"]) &
            (df["assigned_to"].isin(team_members)) &
            (df["state"].isin(["New", "Created", "Evaluate", "Approved", "Active", "In Progress"]))
        ])

        # Non-area KPIs
        all_kpis = [
            ("Total", total_tickets),
            ("Active", active_tickets),
            ("Closed", closed_tickets),
        ]

        all_kpis.append(("Median Days", round(median_days, 1) if pd.notna(median_days) else "N/A"))
        all_kpis.append(("Stale", stale_count_kpi))
        all_kpis.append(("> 2 Weeks", over_2_weeks_count))
        all_kpis.append(("> Month", over_month_count))
        all_kpis.append(("Wrong Area", wrong_area_count))

        # FleetTrack only for Data Modeling team
        if selected_team == "Data Modeling":
            fleet_track_tickets = len(filtered[filtered["tags_list"].apply(lambda t: "Fleet Track" in t)])
            all_kpis.append(("FleetTrack", fleet_track_tickets))

        # Area KPIs from team config
        for ap in team_areas:
            area_name = ap.split("\\")[-1] if "\\" in ap else ap
            all_kpis.append((area_name, count_area(area_name)))

        cols = st.columns(len(all_kpis))
        for col, (label, value) in zip(cols, all_kpis):
            col.metric(label, value)

        # --- Tickets Over Time (Bar Chart, full width) ---
        st.subheader("Tickets Over Time (Created vs Closed)")
        created_ts = (filtered.set_index("created_date")
                      .resample("W").size().reset_index(name="created"))
        closed_ts = (filtered.dropna(subset=["closed_date"])
                     .set_index("closed_date")
                     .resample("W").size().reset_index(name="closed"))
        ts = created_ts.merge(closed_ts, left_on="created_date",
                              right_on="closed_date", how="outer").fillna(0)
        ts["date"] = ts["created_date"].combine_first(ts["closed_date"])
        ts_melted = ts.melt(id_vars=["date"], value_vars=["created", "closed"],
                            var_name="series", value_name="count")
        fig = px.bar(ts_melted, x="date", y="count", color="series", barmode="stack",
                     labels={"count": "Count", "date": "Week", "series": ""})
        fig.update_layout(xaxis_title="Week", yaxis_title="Count")
        st.plotly_chart(fig, use_container_width=True)

        # --- Tickets Older Than 2 Weeks & Tickets by Area ---
        col_old, col_area = st.columns([2, 1])

        with col_old:
            st.subheader("Stale Tickets")
            open_states = [s for s in filtered["state"].unique() if s not in ["Closed", "Resolved", "Done", "Complete"]]
            stale_tickets = filtered[
                (filtered["state"].isin(open_states)) &
                (filtered["assigned_to"].isin(team_members)) &
                (filtered["board_lane"].str.lower() != "backlog") &
                (filtered["age_days"] > 7) &
                (
                    (filtered["days_since_comment"].isna()) |
                    (filtered["days_since_comment"] > 7)
                )
            ]
            if not stale_tickets.empty:
                stale_display = stale_tickets[["id", "title", "assigned_to", "state", "age_days", "days_since_comment"]].copy()
                stale_display.columns = ["ID", "Title", "Assigned To", "State", "Age (Days)", "Stale (Days)"]
                stale_display["Stale (Days)"] = stale_display["Stale (Days)"].apply(
                    lambda x: int(x) if pd.notna(x) else "N/A"
                )
                stale_display = stale_display.sort_values("Age (Days)", ascending=False)
                stale_display["ID"] = stale_display["ID"].apply(lambda x: f"{org_url}/{project}/_workitems/edit/{x}")
                st.dataframe(stale_display, use_container_width=True, hide_index=True,
                    column_config={
                        "ID": st.column_config.LinkColumn(display_text=r"(\d+)$"),
                        "Title": st.column_config.TextColumn(width="small"),
                    })
            else:
                st.info("No stale tickets — all have team comments within the past week.")

        with col_area:
            st.subheader("Tickets by Area")
            if not filtered.empty:
                area_counts = filtered["display_area"].value_counts().reset_index()
                area_counts.columns = ["Area", "Count"]
                area_counts = area_counts.head(15)
                fig_area = px.pie(area_counts, names="Area", values="Count", hole=0.4)
                fig_area.update_traces(textposition="inside", textinfo="label+value")
                fig_area.update_layout(showlegend=False)
                st.plotly_chart(fig_area, use_container_width=True)
            else:
                st.info("No items to display.")

        # --- Team Summary Table ---
        st.divider()
        st.subheader("Team Summary")

        if team_members:
            member_names = sorted(set(team_members) & set(filtered["assigned_to"].dropna().unique()))
        else:
            member_names = sorted(filtered["assigned_to"].dropna().unique())
        team_rows = []

        new_states = ["New", "Created"]
        evaluate_states = ["Evaluate"]
        active_group_states = ["Approved", "Active", "In Progress"]
        complete_states = ["Closed", "Resolved", "Done", "Complete"]
        blocked_states = ["Blocked"]

        for m in member_names:
            m_df = filtered[filtered["assigned_to"] == m]
            m_closed = m_df[m_df["state"].isin(complete_states)]
            m_days = m_closed.apply(
                lambda r: (r["closed_date"] - r["created_date"]).days if pd.notna(r["closed_date"]) else None, axis=1
            ).dropna()

            m_open = m_df[~m_df["state"].isin(complete_states)]
            m_open_no_backlog = m_open[m_open["board_lane"].str.lower() != "backlog"]
            older_week = len(m_open_no_backlog[(m_open_no_backlog["age_days"] > 14) & (m_open_no_backlog["age_days"] <= 30)])
            older_month = len(m_open_no_backlog[m_open_no_backlog["age_days"] > 30])
            stale_count = len(m_open_no_backlog[
                (m_open_no_backlog["age_days"] > 7) &
                ((m_open_no_backlog["days_since_comment"].isna()) | (m_open_no_backlog["days_since_comment"] > 7))
            ])

            avg_comments = m_df["comment_count"].mean() if "comment_count" in m_df.columns and m_df["comment_count"].notna().any() else None

            team_rows.append({
                "Member": m,
                # "Team": member_to_team.get(m, "Unassigned"),
                "Created": int(len(m_df[m_df["state"].isin(new_states)])),
                "Evaluate": int(len(m_df[m_df["state"].isin(evaluate_states)])),
                "Active": int(len(m_df[m_df["state"].isin(active_group_states)])),
                "Complete": int(len(m_closed)),
                "Blocked": int(len(m_df[m_df["state"].isin(blocked_states)])),
                "Avg Days to Complete": round(m_days.mean(), 1) if len(m_days) > 0 else "N/A",
                "Avg Comments/Ticket": round(avg_comments, 1) if pd.notna(avg_comments) else "N/A",
                "> 2 Weeks": int(older_week),
                "> Month": int(older_month),
                "Stale (> 1 Wk)": int(stale_count),
            })

        team_summary_df = pd.DataFrame(team_rows)

        # Style with bright yellow/red text on aging columns
        def style_aging(df):
            styles = pd.DataFrame('', index=df.index, columns=df.columns)
            if "> 2 Weeks" in df.columns:
                styles["> 2 Weeks"] = df["> 2 Weeks"].apply(
                    lambda v: "color: #FFD700; font-weight: bold" if v > 0 else ""
                )
            if "> Month" in df.columns:
                styles["> Month"] = df["> Month"].apply(
                    lambda v: "color: #FF0000; font-weight: bold" if v > 0 else ""
                )
            if "Stale (> 1 Wk)" in df.columns:
                styles["Stale (> 1 Wk)"] = df["Stale (> 1 Wk)"].apply(
                    lambda v: "color: #FFA500; font-weight: bold" if v > 0 else ""
                )
            return styles

        styled_summary = team_summary_df.style.apply(style_aging, axis=None).format({
            "Avg Days to Complete": lambda x: f"{x:.1f}" if isinstance(x, (int, float)) else x,
            "Avg Comments/Ticket": lambda x: f"{x:.1f}" if isinstance(x, (int, float)) else x,
        })
        st.dataframe(styled_summary, use_container_width=True, hide_index=True)

        # --- Team Ticket Details ---
        st.divider()
        st.subheader("Team Ticket Details")

        if team_members:
            all_members = sorted(set(team_members) & set(filtered["assigned_to"].dropna().unique()))
        else:
            all_members = sorted(filtered["assigned_to"].dropna().unique())
        selected_member = st.selectbox("Select Team Member", ["All"] + list(all_members))

        if selected_member == "All":
            if team_members:
                detail_df = filtered[filtered["assigned_to"].isin(team_members)].copy()
            else:
                detail_df = filtered.copy()
        else:
            detail_df = filtered[filtered["assigned_to"] == selected_member].copy()

        state_order = ["New", "Created", "Evaluate", "Approved", "Active", "In Progress", "Blocked"]

        # Filter to only states in state_order
        detail_df = detail_df[detail_df["state"].isin(state_order)]

        # Combine Active, Approved, In Progress -> "Active"
        detail_df["display_status"] = detail_df["state"].replace({
            "Approved": "Active",
            "In Progress": "Active",
        })

        # Sort by age descending
        detail_df = detail_df.sort_values("age_days", ascending=False)

        if not detail_df.empty:
            # display_df = detail_df[["id", "title", "assigned_to", "display_status", "type", "area_path", "created_date", "age_days", "comment_count", "tags"]].copy()
            # display_df.columns = ["ID", "Title", "Assigned To", "Status", "Type", "Area Path", "Created", "Age (Days)", "Comments", "Tags"]
            display_df = detail_df[["id", "title", "assigned_to", "display_status", "type", "area_path", "created_date", "age_days", "comment_count"]].copy()
            display_df.columns = ["ID", "Title", "Assigned To", "Status", "Type", "Area Path", "Created", "Age (Days)", "Comments"]
            display_df["Created"] = display_df["Created"].dt.strftime("%Y-%m-%d")

            def style_old_rows(row):
                if row["Age (Days)"] > 30:
                    return ["color: #FF0000; font-weight: bold"] * len(row)
                return [""] * len(row)

            display_df["ID"] = display_df["ID"].apply(lambda x: f"{org_url}/{project}/_workitems/edit/{x}")
            styled_detail = display_df.style.apply(style_old_rows, axis=1)
            st.dataframe(styled_detail, use_container_width=True, hide_index=True,
                column_config={"ID": st.column_config.LinkColumn(display_text=r"(\d+)$")})
        else:
            st.info("No active tickets to display.")

        # --- Tickets Not in Designated Areas ---
        st.divider()
        st.subheader("Team Tickets Outside Designated Areas")

        active_states = ["New", "Created", "Evaluate", "Approved", "Active", "In Progress"]
        outside_df = df[
            (~df["in_designated_area"]) &
            (df["assigned_to"].isin(team_members)) &
            (df["state"].isin(active_states))
        ].copy()

        if not outside_df.empty:
            outside_df = outside_df.sort_values("age_days", ascending=False)
            outside_display = outside_df[["id", "title", "assigned_to", "state", "area_path", "created_date", "age_days"]].copy()
            outside_display.columns = ["ID", "Title", "Assigned To", "Status", "Area Path", "Created", "Age (Days)"]
            outside_display["Created"] = outside_display["Created"].dt.strftime("%Y-%m-%d")
            outside_display["ID"] = outside_display["ID"].apply(lambda x: f"{org_url}/{project}/_workitems/edit/{x}")
            st.dataframe(outside_display, use_container_width=True, hide_index=True,
                column_config={"ID": st.column_config.LinkColumn(display_text=r"(\d+)$")})
        else:
            st.info("No active team tickets outside designated areas.")

    else:
        st.info("Sign in with your Microsoft account and select a team to get started.")
