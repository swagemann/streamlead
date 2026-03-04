# app.py
import streamlit as st
import plotly.express as px
import pandas as pd
from ado_client import get_credential, get_ado_connection, fetch_work_items, ADO_SCOPE
from teams import load_teams, save_teams, add_member, remove_member

st.set_page_config(page_title="ADO Dashboard", layout="wide")
st.title("Data Modeling Management Dashboard")

# --- Hardcoded Area Paths ---
AREA_PATHS = [
    "Information Management\\BI Dev",
    "Information Management\\dbt",
    # "Information Management\\BI Outbound",
    # "Information Management\\Business Intelligence",
]

# --- Session state for auth ---
if "credential" not in st.session_state:
    st.session_state.credential = None

# --- Sidebar: Config & Filters ---
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
    st.header("Filters")

# --- Data Fetch (cached) ---
@st.cache_data(ttl=300)
def load_data(org_url, token, project, start_date, end_date, area_paths):
    conn = get_ado_connection(org_url, token)
    area_clause = " OR ".join(
        f"[System.AreaPath] UNDER '{project}\\{ap}'" for ap in area_paths
    )
    wiql = f"""
        SELECT [System.Id] FROM WorkItems
        WHERE [System.TeamProject] = '{project}'
        AND ({area_clause})
        AND [System.CreatedDate] >= '{start_date}'
        AND [System.CreatedDate] <= '{end_date}'
        ORDER BY [System.CreatedDate] DESC
    """
    return fetch_work_items(conn, project, wiql)


if st.session_state.credential and project:
    token = st.session_state.credential.get_token(ADO_SCOPE).token
    df = load_data(org_url, token, project, str(date_range[0]), str(date_range[1]), tuple(AREA_PATHS))
    teams = load_teams()

    # Derive tags_list
    df["tags_list"] = df["tags"].apply(
        lambda t: [x.strip() for x in t.split(";") if x.strip()] if t else []
    )

    # Build team lookup
    member_to_team = {}
    for team, members in teams.items():
        for m in members:
            member_to_team[m] = team
    df["team"] = df["assigned_to"].map(member_to_team).fillna("Unassigned")

    # Compute ticket age
    now = pd.Timestamp.now(tz="UTC")
    df["age_days"] = (now - df["created_date"]).dt.days

    # Sidebar filters (dynamic)
    with st.sidebar:
        status_filter = st.multiselect("Status", df["state"].unique(), default=list(df["state"].unique()))
        team_filter = st.multiselect("Team", df["team"].unique(), default=list(df["team"].unique()))
        type_filter = st.multiselect("Work Item Type", df["type"].unique(), default=list(df["type"].unique()))

        all_tags = sorted(set(tag for tags in df["tags_list"] for tag in tags))
        tag_filter = st.multiselect("Tags", all_tags, default=[])

    # Apply filters
    filtered = df[
        (df["state"].isin(status_filter)) &
        (df["team"].isin(team_filter)) &
        (df["type"].isin(type_filter))
    ]
    if tag_filter:
        filtered = filtered[
            filtered["tags_list"].apply(lambda t: bool(set(t) & set(tag_filter)))
        ]

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
    fleet_track_tickets = len(filtered[filtered["tags_list"].apply(lambda t: "Fleet Track" in t)])

    c1, c2, c3, c4, c5, c6, c7, c8, c9 = st.columns(9)
    c1.metric("Total", total_tickets)
    c2.metric("Active", active_tickets)
    c3.metric("Closed", closed_tickets)
    c4.metric("FleetTrack", fleet_track_tickets)
    c5.metric("Median Days to Close", round(median_days, 1) if pd.notna(median_days) else "N/A")
    c6.metric("dbt", count_area("dbt"))
    c7.metric("BI Dev", count_area("BI Dev"))
    c8.metric("IM-BI Outbound", count_area("BI Outbound"))
    c9.metric("IM Business Intelligence", count_area("Business Intelligence"))

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

    # --- Tickets Older Than 2 Weeks & Tickets by Tag ---
    col_old, col_tag = st.columns(2)

    with col_old:
        st.subheader("Tickets Older Than 2 Weeks")
        open_states = [s for s in filtered["state"].unique() if s not in ["Closed", "Resolved", "Done", "Complete"]]
        old_tickets = filtered[(filtered["state"].isin(open_states)) & (filtered["age_days"] > 14)]
        if not old_tickets.empty:
            old_display = old_tickets[["id", "title", "assigned_to", "state", "created_date", "age_days"]].copy()
            old_display.columns = ["ID", "Title", "Assigned To", "State", "Created", "Age (Days)"]
            old_display["Created"] = old_display["Created"].dt.strftime("%Y-%m-%d")
            old_display = old_display.sort_values("Age (Days)", ascending=False)
            st.dataframe(old_display, use_container_width=True, hide_index=True)
        else:
            st.info("No open tickets older than 2 weeks.")

    with col_tag:
        st.subheader("Tickets by Tag")
        tag_df = filtered.explode("tags_list")
        tag_df = tag_df[tag_df["tags_list"].astype(bool)]
        if not tag_df.empty:
            tag_counts = tag_df.groupby("tags_list").size().reset_index(name="count")
            tag_counts = tag_counts.sort_values("count", ascending=False).head(15)
            fig_tags = px.pie(tag_counts, names="tags_list", values="count", hole=0.4,
                              labels={"tags_list": "Tag", "count": "Count"})
            fig_tags.update_traces(textposition="inside", textinfo="label+value")
            st.plotly_chart(fig_tags, use_container_width=True)
        else:
            st.info("No tagged items in the current filter.")

    # --- Team Summary Table ---
    st.divider()
    st.subheader("Team Summary")

    member_names = sorted(filtered["assigned_to"].dropna().unique())
    team_rows = []

    # States mapping
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

        # Open tickets age
        m_open = m_df[~m_df["state"].isin(complete_states)]
        older_week = len(m_open[m_open["age_days"] > 7])
        older_month = len(m_open[m_open["age_days"] > 30])

        avg_comments = m_df["comment_count"].mean() if "comment_count" in m_df.columns and m_df["comment_count"].notna().any() else None

        team_rows.append({
            "Member": m,
            "Team": member_to_team.get(m, "Unassigned"),
            "Created": int(len(m_df[m_df["state"].isin(new_states)])),
            "Evaluate": int(len(m_df[m_df["state"].isin(evaluate_states)])),
            "Approved/Active/In Progress": int(len(m_df[m_df["state"].isin(active_group_states)])),
            "Complete": int(len(m_closed)),
            "Blocked": int(len(m_df[m_df["state"].isin(blocked_states)])),
            "Avg Days to Complete": round(m_days.mean(), 1) if len(m_days) > 0 else "N/A",
            "Avg Comments/Ticket": round(avg_comments, 1) if pd.notna(avg_comments) else "N/A",
            "Older Than Week": int(older_week),
            "Older Than Month": int(older_month),
        })

    team_summary_df = pd.DataFrame(team_rows)
    st.dataframe(team_summary_df, use_container_width=True, hide_index=True)

    # --- Team Ticket Details (replaces Kanban) ---
    st.divider()
    st.subheader("Team Ticket Details")

    all_members = sorted(filtered["assigned_to"].dropna().unique())
    selected_member = st.selectbox("Select Team Member", ["All"] + list(all_members))

    if selected_member == "All":
        detail_df = filtered
    else:
        detail_df = filtered[filtered["assigned_to"] == selected_member]

    # Status ordering
    state_order = ["New", "Created", "Evaluate", "Approved", "Active", "In Progress", "Blocked", "Resolved", "Closed", "Done", "Complete"]
    active_states_in_data = [s for s in state_order if s in detail_df["state"].unique()]
    for s in detail_df["state"].unique():
        if s not in active_states_in_data:
            active_states_in_data.append(s)

    for state_name in active_states_in_data:
        state_items = detail_df[detail_df["state"] == state_name]
        if state_items.empty:
            continue

        st.markdown(f"### {state_name} ({len(state_items)})")

        display_df = state_items[["id", "title", "assigned_to", "type", "area_path", "created_date", "age_days", "comment_count", "tags"]].copy()
        display_df.columns = ["ID", "Title", "Assigned To", "Type", "Area Path", "Created", "Age (Days)", "Comments", "Tags"]
        display_df["Created"] = display_df["Created"].dt.strftime("%Y-%m-%d")

        st.dataframe(display_df, use_container_width=True, hide_index=True)

        st.divider()

    # --- Team Management ---
    st.divider()
    st.subheader("Team Management")

    tab1, tab2 = st.tabs(["View Teams", "Edit Teams"])

    with tab1:
        for team, members in teams.items():
            with st.expander(f"{team} ({len(members)} members)"):
                for m in members:
                    st.write(f"- {m}")

    with tab2:
        col_a, col_b = st.columns(2)
        with col_a:
            st.write("**Add Member**")
            team_name = st.text_input("Team Name")
            member = st.selectbox("Member", df["assigned_to"].dropna().unique())
            if st.button("Add"):
                add_member(team_name, member, teams)
                st.rerun()
        with col_b:
            st.write("**Remove Member**")
            del_team = st.selectbox("From Team", list(teams.keys()) or [""])
            if del_team and teams.get(del_team):
                del_member = st.selectbox("Member to Remove", teams[del_team])
                if st.button("Remove"):
                    remove_member(del_team, del_member, teams)
                    st.rerun()

else:
    st.info("Sign in with your Microsoft account and enter your project name in the sidebar to get started.")
