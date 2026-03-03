# app.py
import streamlit as st
import plotly.express as px
import pandas as pd
from ado_client import get_credential, get_ado_connection, fetch_work_items, ADO_SCOPE
from teams import load_teams, save_teams, add_member, remove_member

st.set_page_config(page_title="ADO Dashboard", layout="wide")
st.title("Azure DevOps Ticket Dashboard")

# --- Session state for auth ---
if "credential" not in st.session_state:
    st.session_state.credential = None

# --- Sidebar: Config & Filters ---
with st.sidebar:
    st.header("🔧 Configuration")
    org_url = st.text_input("Org URL", value="https://dev.azure.com/HOLMAN")
    project = st.text_input("Project", value="IT")

    if st.session_state.credential is None:
        if st.button("Sign In with Microsoft"):
            try:
                cred = get_credential()
                cred.get_token(ADO_SCOPE)  # triggers browser login
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
    st.header("📂 Area Paths")
    area_paths = st.multiselect(
        "Query Area Paths",
        options=[
            "Information Management - BI Dev",
            "Information Management - dbt",
        ],
        default=[
            "Information Management - BI Dev",
            "Information Management - dbt",
        ],
    )

    st.divider()
    st.header("📅 Timeframe")
    date_range = st.date_input("Date Range", value=[
        pd.Timestamp.now() - pd.Timedelta(days=90),
        pd.Timestamp.now()
    ])

    st.divider()
    st.header("🔍 Filters")

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

if st.session_state.credential and project and area_paths:
    token = st.session_state.credential.get_token(ADO_SCOPE).token
    df = load_data(org_url, token, project, str(date_range[0]), str(date_range[1]), tuple(area_paths))
    teams = load_teams()

    # Derive tags_list after cache to avoid serialization issues
    df["tags_list"] = df["tags"].apply(
        lambda t: [x.strip() for x in t.split(";") if x.strip()] if t else []
    )

    # Build team lookup
    member_to_team = {}
    for team, members in teams.items():
        for m in members:
            member_to_team[m] = team
    df["team"] = df["assigned_to"].map(member_to_team).fillna("Unassigned")

    # Sidebar filters (dynamic)
    with st.sidebar:
        status_filter = st.multiselect("Status", df["state"].unique(), default=df["state"].unique())
        team_filter = st.multiselect("Team", df["team"].unique(), default=df["team"].unique())
        type_filter = st.multiselect("Work Item Type", df["type"].unique(), default=df["type"].unique())
        area_filter = st.multiselect("Area Path", df["area_path"].unique(), default=df["area_path"].unique())

        all_tags = sorted(set(tag for tags in df["tags_list"] for tag in tags))
        tag_filter = st.multiselect("Tags", all_tags, default=[])

    # Apply filters
    filtered = df[
        (df["state"].isin(status_filter)) &
        (df["team"].isin(team_filter)) &
        (df["type"].isin(type_filter)) &
        (df["area_path"].isin(area_filter))
    ]
    if tag_filter:
        filtered = filtered[
            filtered["tags_list"].apply(lambda t: bool(set(t) & set(tag_filter)))
        ]

    # --- KPI Row ---
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Tickets", len(filtered))
    c2.metric("Open", len(filtered[filtered["state"].isin(["New", "Active"])]))
    c3.metric("Closed", len(filtered[filtered["closed_date"].notna()]))
    avg_days = filtered.dropna(subset=["closed_date"]).apply(
        lambda r: (r["closed_date"] - r["created_date"]).days, axis=1
    ).mean()
    c4.metric("Avg Days to Close", round(avg_days, 1) if pd.notna(avg_days) else "N/A")
    c5.metric("Fleet Track", len(filtered[
        filtered["tags_list"].apply(lambda t: "Fleet Track" in t)
    ]))

    # --- Charts Row ---
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Tickets Over Time (Created vs Closed)")
        created_ts = (filtered.set_index("created_date")
                      .resample("W").size().reset_index(name="created"))
        closed_ts = (filtered.dropna(subset=["closed_date"])
                     .set_index("closed_date")
                     .resample("W").size().reset_index(name="closed"))
        ts = created_ts.merge(closed_ts, left_on="created_date",
                              right_on="closed_date", how="outer").fillna(0)
        ts["date"] = ts["created_date"].combine_first(ts["closed_date"])
        fig = px.line(ts, x="date", y=["created", "closed"],
                      labels={"value": "Count", "date": "Week"})
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Tickets by Category")
        fig2 = px.pie(filtered, names="type", hole=0.4)
        st.plotly_chart(fig2, use_container_width=True)

    # --- By Team Bar Chart ---
    st.subheader("Tickets by Team & Status")
    team_status = filtered.groupby(["team", "state"]).size().reset_index(name="count")
    fig3 = px.bar(team_status, x="team", y="count", color="state", barmode="stack")
    st.plotly_chart(fig3, use_container_width=True)

    # --- By Area Path Bar Chart ---
    st.subheader("Tickets by Area Path")
    area_counts = filtered.groupby("area_path").size().reset_index(name="count")
    fig_area = px.bar(area_counts, x="area_path", y="count", color="area_path", text_auto=True)
    fig_area.update_layout(showlegend=False)
    st.plotly_chart(fig_area, use_container_width=True)

    # --- By Tag Bar Chart ---
    st.subheader("Tickets by Tag")
    tag_df = filtered.explode("tags_list")
    tag_df = tag_df[tag_df["tags_list"].astype(bool)]
    if not tag_df.empty:
        tag_counts = tag_df.groupby("tags_list").size().reset_index(name="count")
        tag_counts = tag_counts.sort_values("count", ascending=False).head(15)
        fig_tags = px.bar(tag_counts, x="tags_list", y="count", text_auto=True,
                          labels={"tags_list": "Tag", "count": "Count"})
        st.plotly_chart(fig_tags, use_container_width=True)
    else:
        st.info("No tagged items in the current filter.")

    # --- Kanban Board ---
    st.divider()
    st.subheader("Kanban Board")

    state_order = ["New", "Active", "Resolved", "Closed"]
    active_states = [s for s in state_order if s in filtered["state"].unique()]
    for s in filtered["state"].unique():
        if s not in active_states:
            active_states.append(s)

    MAX_CARDS = 20
    kanban_cols = st.columns(len(active_states)) if active_states else []

    for col, state_name in zip(kanban_cols, active_states):
        with col:
            state_items = filtered[filtered["state"] == state_name]
            st.markdown(f"**{state_name}**")
            st.caption(f"{len(state_items)} items")

            for _, row in state_items.head(MAX_CARDS).iterrows():
                with st.expander(f"#{row['id']} - {row['type']}"):
                    st.write(f"**{row.get('title', 'No title')}**")
                    st.write(f"Assigned: {row['assigned_to'] or 'Unassigned'}")
                    st.write(f"Area: {row['area_path']}")
                    if row["tags_list"]:
                        st.write(f"Tags: {', '.join(row['tags_list'])}")
                    created = row["created_date"]
                    st.write(f"Created: {created.strftime('%Y-%m-%d') if pd.notna(created) else 'N/A'}")

            if len(state_items) > MAX_CARDS:
                st.info(f"Showing {MAX_CARDS} of {len(state_items)} items")

    # --- Team Management ---
    st.divider()
    st.subheader("👥 Team Management")

    tab1, tab2 = st.tabs(["View Teams", "Edit Teams"])

    with tab1:
        for team, members in teams.items():
            with st.expander(f"{team} ({len(members)} members)"):
                for m in members:
                    st.write(f"• {m}")

    with tab2:
        col_a, col_b = st.columns(2)
        with col_a:
            st.write("**Add Member**")
            team_name = st.text_input("Team Name")
            # Dropdown from ADO users seen in data
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
    st.info("Sign in with your Microsoft account, enter your project name, and select at least one area path in the sidebar to get started.")
