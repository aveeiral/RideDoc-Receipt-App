"""RideDoc billing app - entry point.  Run with:  streamlit run app.py"""
import streamlit as st

st.set_page_config(page_title="RideDoc Billing", page_icon="🚗", layout="wide")

pages = st.navigation([
    st.Page("views/new_bill.py", title="New Bill", icon="🧾", default=True),
    st.Page("views/history.py", title="Bill History", icon="📚"),
])
pages.run()
