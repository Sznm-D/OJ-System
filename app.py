"""Streamlit frontend. All application data is obtained through the REST API."""
import html
import json
import os
import uuid

import httpx
import streamlit as st
from pydantic import ValidationError

from backend.models import Problem
from frontend_session import sync_browser_cookie

st.set_page_config(page_title="知行 OJ", page_icon="📘", layout="wide", initial_sidebar_state="expanded")
st.markdown("""<style>
[data-testid="stAppViewContainer"] {background: #f5f7fb;}
.stApp {border-top: 3px solid #2f6f72;}
.block-container {max-width: 1180px; padding-top: 2.4rem; padding-bottom: 3rem;}
h1 {font-size: 2rem !important; font-weight: 650 !important; letter-spacing: -.03em; color: #1f2937;}
h2 {font-size: 1.35rem !important; font-weight: 600 !important; color: #253746;}
h3 {font-size: 1.1rem !important; color: #253746;}
[data-testid="stSidebar"] {background: linear-gradient(180deg, #eef5f4 0%, #f6f8f8 58%, #f4f6f9 100%); border-right: 1px solid #dbe5e3;}
[data-testid="stSidebar"] .block-container {padding: 1.45rem 1.1rem 1.2rem;}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {margin-bottom: 0;}
[data-testid="stSidebar"] .stRadio > label {display: none;}
[data-testid="stSidebar"] div[role="radiogroup"] {gap: .28rem;}
[data-testid="stSidebar"] div[role="radio"] {border-radius: 9px; padding: .62rem .75rem; color: #52636a; transition: background .15s ease, color .15s ease;}
[data-testid="stSidebar"] div[role="radio"]:hover {background: rgba(255, 255, 255, .8); color: #244c4e;}
[data-testid="stSidebar"] div[role="radio"][aria-checked="true"] {background: #dcecea; color: #245b5d; font-weight: 650; box-shadow: inset 3px 0 #2f6f72;}
[data-testid="stSidebar"] hr {border-color: #d8e2e0; margin: 1.2rem 0;}
[data-testid="stSidebar"] .stButton > button {border: 1px solid #d4dfdd; background: rgba(255, 255, 255, .72); color: #34595b; box-shadow: none;}
[data-testid="stSidebar"] .stButton > button:hover {border-color: #8db5b0; background: #fff; color: #214c4e;}
[data-testid="stMetric"] {border: 1px solid #e1e8e8; border-radius: 10px; padding: 16px 20px; background: rgba(255, 255, 255, .72); box-shadow: 0 2px 10px rgba(38, 56, 62, .03);}
[data-testid="stMetricValue"] {font-size: 1.7rem;}
.eyebrow {color: #2f6f72; font-size: 14px; letter-spacing: .09em; margin-bottom: .5rem;}
.brand {font-size: 23px; color: #285e60; font-weight: 700; margin: 0 0 .25rem;}
.muted {font-size: 14px; color: #6b7280;}
.sidebar-brand {display: flex; align-items: center; gap: .72rem; padding: .1rem .2rem .3rem;}
.sidebar-mark {display: grid; place-items: center; width: 2.45rem; height: 2.45rem; border-radius: 10px; background: #2f6f72; color: white; font-size: .83rem; font-weight: 750; letter-spacing: .04em; box-shadow: 0 5px 14px rgba(47, 111, 114, .2);}
.sidebar-brand-name {color: #244b4d; font-size: 1.12rem; font-weight: 750; line-height: 1.1;}
.sidebar-brand-subtitle {color: #718184; font-size: .76rem; margin-top: .25rem;}
.sidebar-section-label {color: #8a9a9a; font-size: .68rem; font-weight: 750; letter-spacing: .14em; margin: .2rem .25rem .55rem; text-transform: uppercase;}
.sidebar-account {padding: .85rem .9rem; border: 1px solid #d9e5e2; border-radius: 10px; background: rgba(255, 255, 255, .68);}
.sidebar-account-name {color: #29494b; font-weight: 650; font-size: .94rem;}
.sidebar-account-role {color: #778889; font-size: .76rem; margin-top: .2rem;}
div[data-testid="stForm"] {border-color: #e1e8e8; background: rgba(255, 255, 255, .38);}

/* TAKE YOUR TIME-inspired HUD skin */
@import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;600;700&family=Noto+Sans+SC:wght@400;500;700;900&display=swap');
:root {color-scheme: dark;}
[data-testid="stAppViewContainer"] {background: #000b2e;}
[data-testid="stAppViewContainer"]::before {content: ""; position: fixed; inset: 0; pointer-events: none; background: radial-gradient(circle at 78% 8%, rgba(0, 51, 255, .22), transparent 36%), radial-gradient(circle at 18% 90%, rgba(0, 204, 255, .08), transparent 32%), radial-gradient(circle, rgba(0, 204, 255, .04) 1px, transparent 1.4px); background-size: auto, auto, 9px 9px; z-index: 0;}
.stApp {border-top: 2px solid #00ccff; background: transparent;}
[data-testid="stHeader"] {background: rgba(0, 11, 46, .72);}
.block-container {position: relative; z-index: 1; max-width: 1280px; padding-top: 2.6rem;}
h1, h2, h3, p, label, [data-testid="stCaptionContainer"] {color: #fff !important;}
h1 {font-family: 'Rajdhani', 'Noto Sans SC', sans-serif; font-size: 2.9rem !important; letter-spacing: .02em; text-shadow: 0 0 18px rgba(0, 102, 255, .45);}
h2 {color: #fff !important;}
.eyebrow {color: #00ccff; font-family: 'Rajdhani', 'Noto Sans SC', sans-serif; letter-spacing: .14em; text-shadow: 0 0 12px rgba(0, 204, 255, .5);}
.block-container h1 {display: inline-block; border-bottom: 5px solid #0033ff; padding: 0 .8rem .35rem 0; transform: skewX(-8deg);}
.block-container h1 + div {color: #6c85b8 !important;}
[data-testid="stSidebar"] {width: 20rem !important; background: linear-gradient(90deg, rgba(2, 12, 43, .98), rgba(4, 18, 58, .82)); border-right: 1px solid #1a3470; box-shadow: 14px 0 36px rgba(0, 0, 0, .16);}
[data-testid="stSidebar"] .block-container {padding: 1.55rem 1.35rem 1.2rem 1.65rem;}
[data-testid="stSidebar"] .sidebar-brand {gap: .85rem; padding: .15rem .1rem .45rem;}
[data-testid="stSidebar"] .sidebar-brand-name {font-family: 'Rajdhani', 'Noto Sans SC', sans-serif; color: #fff; font-size: 1.5rem; font-weight: 700; letter-spacing: .1em; line-height: 1; text-shadow: 0 0 14px rgba(0, 51, 255, .8);}
[data-testid="stSidebar"] .sidebar-brand-subtitle {color: #6c85b8; font-size: .7rem; font-weight: 500; letter-spacing: .2em; margin-top: .42rem;}
[data-testid="stSidebar"] .sidebar-mark {width: 2.7rem; height: 2.7rem; border-radius: 0; background: #0033ff; color: #fff; font-family: 'Rajdhani', sans-serif; font-size: .9rem; font-weight: 700; box-shadow: 0 0 18px rgba(0, 51, 255, .72); transform: skewX(-10deg);}
[data-testid="stSidebar"] .sidebar-section-label {color: #6c85b8; font-family: 'Rajdhani', 'Noto Sans SC', sans-serif; font-size: .7rem; font-weight: 700; letter-spacing: .24em; margin: .25rem .15rem .65rem;}
[data-testid="stSidebar"] hr {border-color: #1a3470; margin: 1.1rem 0 1.25rem;}
[data-testid="stSidebar"] div[role="radiogroup"] {gap: .45rem;}
[data-testid="stSidebar"] div[role="radio"] {position: relative; min-height: 2.72rem; border: 1px solid #1a3470; border-radius: 4px; padding: .72rem .85rem .72rem 2.3rem; color: #4fc9ef; background: rgba(4, 18, 58, .52); font-family: 'Noto Sans SC', sans-serif; font-size: .93rem; font-weight: 500; letter-spacing: .08em; line-height: 1.25; transform: skewX(-5deg); transition: color .15s ease, background .15s ease, border-color .15s ease, padding-left .15s ease, box-shadow .15s ease;}
[data-testid="stSidebar"] div[role="radio"]::before {position: absolute; left: .85rem; top: 50%; width: 1rem; color: #6c85b8; font-family: 'Rajdhani', sans-serif; font-size: .72rem; font-weight: 700; letter-spacing: .04em; transform: translateY(-50%) skewX(5deg);}
[data-testid="stSidebar"] div[role="radio"]:nth-child(1)::before {content: "01";}
[data-testid="stSidebar"] div[role="radio"]:nth-child(2)::before {content: "02";}
[data-testid="stSidebar"] div[role="radio"]:nth-child(3)::before {content: "03";}
[data-testid="stSidebar"] div[role="radio"]:nth-child(4)::before {content: "04";}
[data-testid="stSidebar"] div[role="radio"]:nth-child(5)::before {content: "05";}
[data-testid="stSidebar"] div[role="radio"]:nth-child(6)::before {content: "06";}
[data-testid="stSidebar"] div[role="radio"]:nth-child(7)::before {content: "07";}
[data-testid="stSidebar"] div[role="radio"] > label {transform: skewX(5deg);}
[data-testid="stSidebar"] div[role="radio"]:hover {background: rgba(0, 51, 255, .34); border-color: #00ccff; color: #fff; padding-left: 2.45rem; box-shadow: 0 0 12px rgba(0, 102, 255, .2);}
[data-testid="stSidebar"] div[role="radio"][aria-checked="true"] {background: #ff0033; border-color: #ff0033; color: #fff; font-weight: 700; box-shadow: 5px 5px 0 #ff6bb4;}
[data-testid="stSidebar"] div[role="radio"][aria-checked="true"]::before {color: #fff;}
[data-testid="stSidebar"] .sidebar-account {margin-top: .15rem; border-radius: 0; border-color: #1a3470; background: rgba(10, 31, 85, .68); padding: .9rem 1rem; transform: skewX(-4deg);}
[data-testid="stSidebar"] .sidebar-account-name {color: #fff; font-family: 'Noto Sans SC', sans-serif; font-size: .94rem; font-weight: 700; letter-spacing: .04em;}
[data-testid="stSidebar"] .sidebar-account-role {color: #6c85b8; font-size: .72rem; font-weight: 500; letter-spacing: .12em; margin-top: .35rem;}
[data-testid="stSidebar"] .stButton > button {border-radius: 0; border-color: #1a3470; background: rgba(10, 31, 85, .72); color: #00ccff; font-family: 'Noto Sans SC', sans-serif; font-size: .84rem; font-weight: 600; letter-spacing: .12em; transform: skewX(-5deg);}
[data-testid="stSidebar"] .stButton > button:hover {border-color: #00ccff; background: #0033ff; color: #fff;}
[data-testid="stMetric"] {border-radius: 0; border-color: #1a3470; background: rgba(4, 18, 58, .82); box-shadow: inset 4px 0 #0033ff, 0 0 18px rgba(0, 51, 255, .12);}
[data-testid="stMetricLabel"] {color: #6c85b8 !important;}
[data-testid="stMetricValue"] {color: #00ccff !important; text-shadow: 0 0 12px rgba(0, 204, 255, .5);}
div[data-testid="stForm"], [data-testid="stExpander"] {border-radius: 0; border-color: #1a3470; background: rgba(4, 18, 58, .74);}
div[data-testid="stTextInput"] input, div[data-testid="stTextArea"] textarea, div[data-baseweb="select"] > div, div[data-testid="stNumberInput"] input {border-radius: 0; border-color: #1a3470; background: rgba(0, 11, 46, .76); color: #fff;}
button[kind="primary"] {border-radius: 0 !important; background: #ff0033 !important; border-color: #ff0033 !important; color: #fff !important; box-shadow: 5px 5px 0 rgba(255, 107, 180, .62); transform: skewX(-7deg);}
button[kind="primary"]:hover {background: #ff4065 !important; border-color: #ff4065 !important;}
[data-testid="stDataFrame"] {border: 1px solid #1a3470;}

/* High-contrast form copy and placeholders. */
input, textarea, [data-baseweb="select"] *, [data-testid="stTextInput"] label, [data-testid="stTextArea"] label, [data-testid="stNumberInput"] label, [data-testid="stSelectbox"] label {color: #fff !important;}
input::placeholder, textarea::placeholder {color: rgba(255, 255, 255, .78) !important; opacity: 1 !important;}
[data-testid="stCaptionContainer"], [data-testid="stTextInput"] small, [data-testid="stTextArea"] small, [data-testid="stNumberInput"] small {color: rgba(255, 255, 255, .82) !important;}
[data-testid="stRadio"] > label {color: #fff !important;}
[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea, [data-testid="stNumberInput"] input {background: #fff !important; color: #111827 !important;}
[data-testid="stTextInput"] input::placeholder, [data-testid="stTextArea"] textarea::placeholder, [data-testid="stNumberInput"] input::placeholder {color: #111827 !important; opacity: .82 !important;}

/* Problem list: compact button-like choices instead of default radio controls. */
.block-container [data-testid="stRadio"] > div {gap: .5rem;}
.block-container [data-testid="stRadio"] label[data-baseweb="radio"] {display: flex; min-height: 3.15rem; width: 100%; align-items: center; border: 1px solid #1a3470; border-radius: 4px; padding: .65rem .85rem .65rem 1rem; background: rgba(4, 18, 58, .62); color: #d6f6ff; cursor: pointer; font-family: 'Noto Sans SC', sans-serif; font-size: .9rem; font-weight: 500; line-height: 1.35; transition: background .15s ease, border-color .15s ease, color .15s ease, transform .15s ease, box-shadow .15s ease;}
.block-container [data-testid="stRadio"] label[data-baseweb="radio"] > div:first-child {display: none;}
.block-container [data-testid="stRadio"] label[data-baseweb="radio"] > div:last-child {width: 100%;}
.block-container [data-testid="stRadio"] label[data-baseweb="radio"]::before {content: ""; width: 3px; align-self: stretch; margin-right: .7rem; background: #1a3470; transition: background .15s ease;}
.block-container [data-testid="stRadio"] label[data-baseweb="radio"]:hover {background: rgba(0, 51, 255, .34); border-color: #00ccff; color: #fff; transform: translateX(3px); box-shadow: 0 0 14px rgba(0, 102, 255, .2);}
.block-container [data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked) {background: #0033ff; border-color: #00ccff; color: #fff; font-weight: 700; box-shadow: 5px 5px 0 rgba(255, 0, 51, .72);}
.block-container [data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked)::before {background: #ff0033;}
.block-container [data-testid="stButton"] > button[kind="secondary"] {display: flex; min-height: 3.15rem; align-items: center; justify-content: flex-start; width: 100%; margin: 0 0 .5rem; padding: .65rem 1rem; border: 1px solid #1a3470; border-radius: 4px; background: rgba(4, 18, 58, .62); color: #d6f6ff; font-family: 'Noto Sans SC', sans-serif; font-size: .9rem; font-weight: 500; line-height: 1.35; box-shadow: none; transform: skewX(-5deg); transition: background .15s ease, border-color .15s ease, color .15s ease, transform .15s ease, box-shadow .15s ease;}
.block-container [data-testid="stButton"] > button[kind="secondary"]:hover {background: rgba(0, 51, 255, .34); border-color: #00ccff; color: #fff; transform: translateX(3px) skewX(-5deg); box-shadow: 0 0 14px rgba(0, 102, 255, .2);}
.block-container [data-testid="stButton"] > button[kind="primary"] {min-height: 3.15rem; justify-content: flex-start; margin: 0 0 .5rem; padding: .65rem 1rem; font-size: .9rem; letter-spacing: .02em;}

/* Streamlit BaseWeb radio: render the workbench as real menu buttons. */
[data-testid="stSidebar"] [data-testid="stRadio"] > div {gap: .45rem;}
[data-testid="stSidebar"] label[data-baseweb="radio"] {position: relative; display: flex; min-height: 2.72rem; width: 100%; align-items: center; border: 1px solid #1a3470; border-radius: 4px; padding: .72rem .85rem .72rem 2.3rem; background: rgba(4, 18, 58, .52); color: #4fc9ef; cursor: pointer; font-family: 'Noto Sans SC', sans-serif; font-size: .93rem; font-weight: 500; letter-spacing: .08em; line-height: 1.25; transform: skewX(-5deg); transition: color .15s ease, background .15s ease, border-color .15s ease, box-shadow .15s ease, padding-left .15s ease;}
[data-testid="stSidebar"] label[data-baseweb="radio"] > div:first-child {display: none;}
[data-testid="stSidebar"] label[data-baseweb="radio"] > div:last-child {transform: skewX(5deg);}
[data-testid="stSidebar"] label[data-baseweb="radio"]::before {position: absolute; left: .85rem; top: 50%; color: #6c85b8; font-family: 'Rajdhani', sans-serif; font-size: .72rem; font-weight: 700; letter-spacing: .04em; transform: translateY(-50%) skewX(5deg);}
[data-testid="stSidebar"] label[data-baseweb="radio"]:nth-child(1)::before {content: "01";}
[data-testid="stSidebar"] label[data-baseweb="radio"]:nth-child(2)::before {content: "02";}
[data-testid="stSidebar"] label[data-baseweb="radio"]:nth-child(3)::before {content: "03";}
[data-testid="stSidebar"] label[data-baseweb="radio"]:nth-child(4)::before {content: "04";}
[data-testid="stSidebar"] label[data-baseweb="radio"]:nth-child(5)::before {content: "05";}
[data-testid="stSidebar"] label[data-baseweb="radio"]:nth-child(6)::before {content: "06";}
[data-testid="stSidebar"] label[data-baseweb="radio"]:nth-child(7)::before {content: "07";}
[data-testid="stSidebar"] label[data-baseweb="radio"]:hover {background: rgba(0, 51, 255, .34); border-color: #00ccff; color: #fff; padding-left: 2.45rem; box-shadow: 0 0 12px rgba(0, 102, 255, .2);}
[data-testid="stSidebar"] label[data-baseweb="radio"]:has(input:checked) {background: #ff0033; border-color: #ff0033; color: #fff; font-weight: 700; box-shadow: 5px 5px 0 #ff6bb4;}
[data-testid="stSidebar"] label[data-baseweb="radio"]:has(input:checked)::before {color: #fff;}
[data-testid="stSidebar"] [data-testid="stRadio"] {display: none;}
[data-testid="stSidebar"] [data-testid="stButton"] {margin-bottom: .45rem;}
[data-testid="stSidebar"] [data-testid="stButton"] > button {display: flex; min-height: 2.72rem; align-items: center; justify-content: flex-start; width: 100%; margin: 0; padding: .72rem .85rem; border: 1px solid #1a3470; border-radius: 4px; background: rgba(4, 18, 58, .52); color: #4fc9ef; font-family: 'Noto Sans SC', sans-serif; font-size: .93rem; font-weight: 500; letter-spacing: .08em; line-height: 1.25; box-shadow: none; transform: skewX(-5deg); transition: color .15s ease, background .15s ease, border-color .15s ease, box-shadow .15s ease, padding-left .15s ease;}
[data-testid="stSidebar"] [data-testid="stButton"] > button:hover {background: rgba(0, 51, 255, .34); border-color: #00ccff; color: #fff; padding-left: 1.05rem; box-shadow: 0 0 12px rgba(0, 102, 255, .2);}
[data-testid="stSidebar"] [data-testid="stButton"] > button[kind="primary"] {background: #ff0033; border-color: #ff0033; color: #fff; font-weight: 700; box-shadow: 5px 5px 0 #ff6bb4;}
/* Keep the submission language readable on its light control and popup. */
.st-key-submission_language [data-baseweb="select"] > div {background: #fff !important;}
.st-key-submission_language .react-aria-ComboBox [role="group"],
.st-key-submission_language .react-aria-ComboBox input {background: #fff !important;}
.st-key-submission_language .react-aria-ComboBox *,
.st-key-submission_language [data-baseweb="select"],
.st-key-submission_language [data-baseweb="select"] * {color: #000 !important; -webkit-text-fill-color: #000 !important;}
[role="listbox"][aria-label="语言"],
[role="listbox"][aria-label="语言"] [role="option"],
[data-baseweb="popover"] [role="listbox"],
[data-baseweb="popover"] [role="option"] {background: #fff !important;}
[role="listbox"][aria-label="语言"],
[role="listbox"][aria-label="语言"] *,
[data-baseweb="popover"] [role="listbox"],
[data-baseweb="popover"] [role="listbox"] * {color: #000 !important; -webkit-text-fill-color: #000 !important;}
[role="listbox"][aria-label="语言"] [role="option"]:hover,
[role="listbox"][aria-label="语言"] [role="option"][data-focused="true"],
[role="listbox"][aria-label="语言"] [role="option"][aria-selected="true"],
[data-baseweb="popover"] [role="option"]:hover,
[data-baseweb="popover"] [role="option"][aria-selected="true"] {background: #e7eef8 !important;}

/* Readable white controls: submission filter/status, detail picker, editor picker. */
.st-key-submission_status [data-baseweb="select"] > div,
.st-key-submission_detail [data-baseweb="select"] > div,
.st-key-edit_problem [data-baseweb="select"] > div {background: #fff !important;}
.st-key-submission_status .react-aria-ComboBox [role="group"],
.st-key-submission_status .react-aria-ComboBox input,
.st-key-submission_detail .react-aria-ComboBox [role="group"],
.st-key-submission_detail .react-aria-ComboBox input,
.st-key-edit_problem .react-aria-ComboBox [role="group"],
.st-key-edit_problem .react-aria-ComboBox input {background: #fff !important;}
.st-key-submission_status .react-aria-ComboBox *,
.st-key-submission_status [data-baseweb="select"],
.st-key-submission_status [data-baseweb="select"] *,
.st-key-submission_detail .react-aria-ComboBox *,
.st-key-submission_detail [data-baseweb="select"],
.st-key-submission_detail [data-baseweb="select"] *,
.st-key-edit_problem .react-aria-ComboBox *,
.st-key-edit_problem [data-baseweb="select"],
.st-key-edit_problem [data-baseweb="select"] * {color: #000 !important; -webkit-text-fill-color: #000 !important;}

/* The visibility form's secondary submit button defaults to white-on-white. */
.st-key-log_visibility_submit button {background: #fff !important; color: #000 !important; -webkit-text-fill-color: #000 !important; border-color: #1a3470 !important;}
.st-key-log_visibility_submit button:hover {background: #e7eef8 !important; color: #000 !important;}

/* Scope white statement text to the problem, including Markdown and samples. */
.st-key-problem_statement, .st-key-problem_statement * {color: #fff !important; -webkit-text-fill-color: #fff !important;}
.st-key-problem_statement pre, .st-key-problem_statement code,
.st-key-problem_statement [data-testid="stCode"] {background: #04123a !important;}
/* Shared contrast rules: dark page text stays white, light controls stay black. */
.stApp :is(h1, h2, h3, h4, h5, h6, p, label, li, a, small, summary),
.stApp :is([data-testid="stCaptionContainer"], [data-testid="stMetricLabel"],
           [data-testid="stMetricValue"], [data-testid="stMarkdownContainer"],
           [data-testid="stAlert"], [data-testid="stExpander"], [role="tab"]),
.stApp :is(.eyebrow, .muted, .sidebar-brand-subtitle, .sidebar-account-role) {
    color: #fff !important;
    -webkit-text-fill-color: #fff !important;
}
.stApp :is([data-testid="stCaptionContainer"], [data-testid="stMetricLabel"],
           [data-testid="stMetricValue"], [data-testid="stAlert"], [role="tab"]) * {
    color: #fff !important;
    -webkit-text-fill-color: #fff !important;
}
.stApp [data-testid="stCode"], .stApp pre, .stApp code {
    background: #04123a !important;
    color: #fff !important;
    -webkit-text-fill-color: #fff !important;
}
.stApp [data-testid="stCode"] * {
    color: #fff !important;
    -webkit-text-fill-color: #fff !important;
}
.stApp :is([data-testid="stTextInput"], [data-testid="stTextArea"],
           [data-testid="stNumberInput"]) :is(input, textarea, button),
.stApp .react-aria-ComboBox [role="group"],
.stApp [data-baseweb="select"] > div {
    background: #fff !important;
    color: #000 !important;
    -webkit-text-fill-color: #000 !important;
}
.stApp .react-aria-ComboBox *, .stApp [data-baseweb="select"] *,
.stApp :is([data-testid="stTextInput"], [data-testid="stNumberInput"]) button * {
    color: #000 !important;
    -webkit-text-fill-color: #000 !important;
}
.stApp :is(input, textarea)::placeholder {
    color: #000 !important;
    -webkit-text-fill-color: #000 !important;
    opacity: 1 !important;
}
[role="listbox"], [role="listbox"] [role="option"] {
    background: #fff !important;
}
[role="listbox"], [role="listbox"] * {
    color: #000 !important;
    -webkit-text-fill-color: #000 !important;
}
[role="listbox"] [role="option"]:is(:hover, [data-focused="true"], [aria-selected="true"]) {
    background: #e7eef8 !important;
}
.stApp [data-testid="stFormSubmitButton"] button[kind="secondary"],
.stApp [data-testid="stFormSubmitButton"] button[kind="secondary"] *,
.stApp .st-key-log_visibility_submit button * {
    background: #fff !important;
    color: #000 !important;
    -webkit-text-fill-color: #000 !important;
}
/* Expander header bars stay light; force black titles so they read against the theme. */
.stApp [data-testid="stExpander"] summary {
    background: #fff !important;
    color: #000 !important;
    -webkit-text-fill-color: #000 !important;
}
.stApp [data-testid="stExpander"] summary * {
    color: #000 !important;
    -webkit-text-fill-color: #000 !important;
}
.stApp [data-testid="stExpander"] summary:hover {
    background: #e7eef8 !important;
}
/* Expander content inherits the light theme's white background; darken it so white text reads. */
.stApp [data-testid="stExpander"] details,
.stApp [data-testid="stExpanderDetails"] {
    background: rgba(4, 18, 58, .74) !important;
}
</style>""", unsafe_allow_html=True)


class APIError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def api(method, path, **kwargs):
    base_url = os.getenv("OJ_API_URL", "http://127.0.0.1:8001")
    if "client" in st.session_state and str(st.session_state.client.base_url).rstrip("/") != base_url.rstrip("/"):
        st.session_state.client.close()
        del st.session_state["client"]
    if "client" not in st.session_state:
        st.session_state.client = httpx.Client(base_url=base_url, timeout=20, follow_redirects=True, trust_env=False)
    try:
        result = st.session_state.client.request(method, "/api" + path, **kwargs)
        payload = result.json()
    except (httpx.HTTPError, ValueError):
        raise APIError(503, "暂时无法连接后端服务，请确认后端已启动。") from None
    if result.status_code != payload.get("code") or result.status_code >= 400:
        raise APIError(result.status_code, payload.get("msg", "请求失败"))
    return payload["data"]


def go(page):
    st.session_state.next_page = page
    st.rerun()


def heading(title, description):
    st.markdown('<div class="eyebrow">程序设计训练 / ONLINE JUDGE</div>', unsafe_allow_html=True)
    st.title(title)
    st.caption(description)


def authentication_page():
    heading("从一道题开始", "登录后浏览题库、提交代码，查看每一次练习的结果。")
    left, right = st.columns([1.15, 1], gap="large")
    with left:
        sign_in, sign_up = st.tabs(["登录", "注册"])
        with sign_in, st.form("login"):
            username = st.text_input("用户名", key="login_name")
            password = st.text_input("密码", type="password", key="login_password")
            if st.form_submit_button("登录", type="primary", use_container_width=True):
                user = api("POST", "/auth/login", json={"username": username, "password": password})
                ticket = api("POST", "/auth/browser-ticket")["ticket"]
                client = st.session_state.client
                st.session_state.clear()
                st.session_state.client, st.session_state.user = client, user
                st.session_state.auth_restore_attempted = True
                st.session_state.browser_cookie_pending = {"ticket": ticket, "id": uuid.uuid4().hex}
                st.rerun()
        with sign_up, st.form("register"):
            name = st.text_input("用户名（3–40 个字符）")
            password = st.text_input("密码（至少 6 位）", type="password")
            repeated = st.text_input("再次输入密码", type="password")
            if st.form_submit_button("创建账户", type="primary"):
                if password != repeated:
                    st.error("两次输入的密码不一致。")
                else:
                    api("POST", "/users/", json={"username": name, "password": password})
                    st.success("注册成功，请切换到登录。")
    with right:
        with st.container(border=True):
            st.subheader("练习与命题")
            st.markdown("**01　阅读题目**\n\n查看输入输出格式、数据范围和样例。\n\n**02　提交解答**\n\n支持 Python 与 C++，自动评测并保存结果。\n\n**03　设计新题**\n\n编辑完整题目，或用 AI 辅助生成和验证测试数据。")


def problem_detail(problem):
    with st.container(key="problem_statement"):
        render_problem_statement(problem)


def render_problem_statement(problem):
    st.subheader(problem["title"])
    tags = " · ".join(problem.get("tags", []))
    st.caption(f"{problem['id']}　 /　{problem.get('difficulty') or '未分级'}　 /　{problem['time_limit']:g} s　 /　{problem['memory_limit']} MB" + (f"　 /　{tags}" if tags else ""))
    st.markdown(problem["description"])
    a, b = st.columns(2)
    with a:
        st.markdown("**输入格式**")
        st.write(problem["input_description"])
    with b:
        st.markdown("**输出格式**")
        st.write(problem["output_description"])
    st.markdown("**数据范围**")
    st.write(problem["constraints"])
    for i, sample in enumerate(problem["samples"], 1):
        x, y = st.columns(2)
        with x:
            st.caption(f"样例 {i} · 输入")
            st.code(sample["input"], language="text")
        with y:
            st.caption(f"样例 {i} · 输出")
            st.code(sample["output"], language="text")
    if problem.get("hint"):
        with st.expander("提示"):
            st.write(problem["hint"])


VERDICTS = {"AC": "通过", "WA": "答案错误", "TLE": "超时", "MLE": "内存超限", "RE": "运行错误", "CE": "编译错误", "UNK": "未知错误"}


@st.fragment(run_every=1.5 if st.session_state.get("poll_submissions", True) else None)
def submission_panel(sid):
    try:
        row = api("GET", f"/submissions/{sid}")
        st.caption(f"提交编号：{sid}")
        if row["status"] == "pending":
            if not st.session_state.get("poll_submissions", True):
                st.session_state.poll_submissions = True
                st.rerun()
            st.info("评测中 · 页面会自动更新")
            return
        if st.session_state.get("poll_submissions", True):
            st.session_state.poll_submissions = False
            st.rerun()
        verdict = (row.get("run_info") or {}).get("result", "UNK")
        if row["status"] == "error":
            st.error(row.get("error_info") or "评测执行失败")
        elif verdict == "AC":
            st.success(f"AC · 通过　{row['score']} / {row['counts']} 分")
        else:
            st.warning(f"{verdict} · {VERDICTS.get(verdict, verdict)}　{row['score']} / {row['counts']} 分")
        with st.expander("编译与运行信息", expanded=verdict in {"CE", "RE", "UNK"}):
            compile_info = row.get("compile_info")
            if compile_info:
                st.write("编译：" + compile_info["result"])
                if compile_info.get("message"):
                    st.code(compile_info["message"], language="text")
            else:
                st.caption("解释型语言无需单独编译。")
            if row.get("run_info"):
                st.write(row["run_info"]["message"])
            if row.get("error_info"):
                st.error(row["error_info"])
        if st.button("查看测试点日志", key="logs_" + sid):
            logs = api("GET", f"/submissions/{sid}/log")
            if "details" in logs:
                st.dataframe(logs["details"], hide_index=True, use_container_width=True)
                st.caption("time：秒；memory：MB。容器模式中内存峰值未采集时显示空值。")
            else:
                st.info("当前日志仅展示总分，管理员尚未公开测试点明细。")
        if st.session_state.user["role"] == "admin" and st.button("重新评测", key="rejudge_" + sid):
            api("PUT", f"/submissions/{sid}/rejudge")
            st.rerun()
    except APIError as exc:
        st.error(str(exc))


def problems_page():
    heading("题库", "选择一道题，阅读题面后提交解答。")
    problems = api("GET", "/problems/")
    search = st.text_input("搜索题目", placeholder="输入题号或标题", label_visibility="collapsed")
    filtered = [p for p in problems if search.lower() in (p["id"] + p["title"]).lower()]
    if not filtered:
        st.info("没有符合条件的题目。可在题目管理中新增。")
        return
    menu, content = st.columns([1, 2.6], gap="large")
    with menu:
        st.caption(f"共 {len(filtered)} 道题目")
        problem_ids = [p["id"] for p in filtered]
        if st.session_state.get("selected_problem") not in problem_ids:
            st.session_state.selected_problem = problem_ids[0]
        for problem in filtered:
            problem_id = problem["id"]
            if st.button(f"{problem_id}  {problem['title']}", key=f"problem-{problem_id}", type="primary" if problem_id == st.session_state.selected_problem else "secondary", use_container_width=True):
                st.session_state.selected_problem = problem_id
                st.rerun()
        pid = st.session_state.selected_problem
    with content:
        problem = api("GET", f"/problems/{pid}")
        problem_detail(problem)
        st.divider()
        st.subheader("提交解答")
        languages = api("GET", "/languages/")["name"]
        with st.form("submit_" + pid):
            language = st.selectbox("语言", languages, key="submission_language")
            code = st.text_area("代码", height=220, placeholder="在这里粘贴完整代码，从标准输入读取并输出答案。")
            st.caption("每分钟最多提交 3 次；每通过一个测试点得 10 分。")
            if st.form_submit_button("提交评测", type="primary"):
                result = api("POST", "/submissions/", json={"problem_id": pid, "language": language, "code": code})
                st.session_state.last_submission = result["submission_id"]
                st.session_state.poll_submissions = True
                st.rerun()
        if st.session_state.get("last_submission"):
            submission_panel(st.session_state.last_submission)


def submissions_page():
    heading("提交记录", "筛选历史提交，查看状态、得分与评测日志。")
    user = st.session_state.user
    with st.form("filters"):
        a, b, c, d = st.columns([2, 2, 1, 1])
        pid = a.text_input("题目编号", placeholder="全部题目")
        uid = b.text_input("用户编号", value=user["user_id"], disabled=user["role"] != "admin")
        status = c.selectbox("状态", ["全部", "pending", "success", "error"], key="submission_status")
        page = d.number_input("页码", min_value=1, step=1)
        st.form_submit_button("查询", type="primary")
    params = {"page": page, "page_size": 15}
    if pid:
        params["problem_id"] = pid
    if uid:
        params["user_id"] = uid
    if status != "全部":
        params["status"] = status
    result = api("GET", "/submissions/", params=params)
    st.caption(f"共 {result['total']} 次提交")
    st.dataframe(result["submissions"], hide_index=True, use_container_width=True)
    options = [r["submission_id"] for r in result["submissions"]]
    if options:
        sid = st.selectbox("选择提交查看详情", options, key="submission_detail")
        submission_panel(sid)
    with st.expander("按编号查询公开日志"):
        sid = st.text_input("提交编号", key="public_sid")
        if st.button("查询日志"):
            logs = api("GET", f"/submissions/{sid}/log")
            st.write(f"得分：{logs['score']} / {logs['counts']}")
            if "details" in logs:
                st.dataframe(logs["details"], hide_index=True, use_container_width=True)


EMPTY_PROBLEM = {"id": "", "title": "", "description": "", "input_description": "", "output_description": "", "constraints": "", "samples": [{"input": "", "output": ""}], "testcases": [{"input": "", "output": ""}]}


def editor(problem, editing=False, key="editor"):
    with st.form(key):
        a, b = st.columns([1, 3])
        values = {"id": a.text_input("题目编号", value=problem.get("id", ""), disabled=editing), "title": b.text_input("标题", value=problem.get("title", ""))}
        values["description"] = st.text_area("题目描述", value=problem.get("description", ""), height=140)
        a, b = st.columns(2)
        values["input_description"] = a.text_area("输入格式", value=problem.get("input_description", ""))
        values["output_description"] = b.text_area("输出格式", value=problem.get("output_description", ""))
        values["constraints"] = st.text_input("数据范围", value=problem.get("constraints", ""))
        a, b, c = st.columns(3)
        values["difficulty"] = a.text_input("难度", value=problem.get("difficulty", "入门"))
        values["time_limit"] = b.number_input("时间限制（秒，0 为继承语言默认值）", min_value=0.0, max_value=30.0, value=float(problem.get("time_limit") or 0), step=0.1) or None
        values["memory_limit"] = c.number_input("内存限制（MB，0 为继承）", min_value=0, max_value=1024, value=problem.get("memory_limit") or 0, step=16) or None
        samples, tests, extra = st.tabs(["样例", "测试点", "补充信息"])
        with samples:
            sample_json = st.text_area("样例 JSON", value=json.dumps(problem["samples"], ensure_ascii=False, indent=2), height=200)
        with tests:
            test_json = st.text_area("测试点 JSON", value=json.dumps(problem["testcases"], ensure_ascii=False, indent=2), height=250)
            st.caption('使用 [{"input": "1 2", "output": "3"}] 格式；每个测试点 10 分。')
        with extra:
            values["tags"] = [t.strip() for t in st.text_input("标签（逗号分隔）", value=", ".join(problem.get("tags", []))).replace("，", ",").split(",") if t.strip()]
            values["hint"] = st.text_area("提示", value=problem.get("hint", ""))
            values["source"] = st.text_input("来源", value=problem.get("source", ""))
            values["author"] = st.text_input("作者", value=problem.get("author", ""))
        if st.form_submit_button("保存修改" if editing else "添加题目", type="primary"):
            try:
                values["samples"], values["testcases"] = json.loads(sample_json), json.loads(test_json)
                parsed = Problem.model_validate(values).model_dump()
            except (ValueError, ValidationError):
                st.error("题目配置有误，请检查必填字段、资源限制及样例/测试点的 JSON 格式。")
            else:
                api("PUT" if editing else "POST", f"/problems/{parsed['id']}" if editing else "/problems/", json=parsed)
                st.success("题目已保存，可在题库中查看和提交。")


def problem_management():
    heading("题目管理", "维护题面、样例与测试点，也可导入 AI 生成的草稿。")
    create, edit, language = st.tabs(["新增题目", "编辑与权限", "语言管理"])
    with create:
        draft = st.session_state.get("ai_draft")
        if draft:
            st.info("已载入 AI 草稿，请审阅后保存。")
        imported = st.file_uploader("导入题目 JSON（可选）", type=["json"])
        source = draft or EMPTY_PROBLEM
        if imported:
            try:
                source = Problem.model_validate_json(imported.getvalue()).model_dump()
            except ValueError:
                st.error("导入的 JSON 不符合题目格式。")
        editor(source, key="new_" + str(st.session_state.get("draft_version", 0)) + (imported.name if imported else ""))
    with edit:
        problems = api("GET", "/problems/")
        if problems:
            pid = st.selectbox("选择要编辑的题目", [p["id"] for p in problems], key="edit_problem", format_func=lambda x: next(p["id"] + " · " + p["title"] for p in problems if p["id"] == x))
            problem = api("GET", f"/problems/{pid}")
            use_draft = st.checkbox("用 AI 草稿填充本题编辑器", disabled=not bool(st.session_state.get("ai_draft")))
            if use_draft:
                problem = dict(st.session_state.ai_draft, id=pid)
            editor(problem, editing=True, key=f"edit_{pid}_{use_draft}_{st.session_state.get('draft_version', 0)}")
            if st.session_state.user["role"] == "admin":
                st.divider()
                visibility = api("GET", f"/problems/{pid}/log_visibility")
                with st.form("visibility_" + pid):
                    public = st.checkbox("向所有登录用户公开本题评测日志与测试点明细", value=visibility["public_cases"])
                    if st.form_submit_button("更新日志可见性", key="log_visibility_submit"):
                        api("PUT", f"/problems/{pid}/log_visibility", json={"public_cases": public})
                        st.success("日志可见性已更新。")
                with st.expander("删除题目"):
                    acknowledged = st.checkbox("确认删除当前题目（历史提交仍保留）", key="delete_" + pid)
                    if st.button("删除此题", disabled=not acknowledged):
                        api("DELETE", f"/problems/{pid}")
                        st.rerun()
        else:
            st.info("题库为空，请先新增题目。")
    with language:
        st.write("已注册：" + "、".join(api("GET", "/languages/")["name"]))
        with st.form("language"):
            st.caption("可注册 Python 或 C++ 的语言变体；执行命令采用受限模板。")
            name = st.text_input("语言名称", value="cpp17")
            ext = st.text_input("文件扩展名", value=".cpp")
            compile_cmd = st.text_input("编译命令", value="g++ {src} -O2 -std=c++17 -o {exe}")
            run_cmd = st.text_input("运行命令", value="{exe}")
            a, b = st.columns(2)
            seconds = a.number_input("默认时间（秒）", min_value=0.1, max_value=30.0, value=3.0)
            memory = b.number_input("默认内存（MB）", min_value=16, max_value=1024, value=128)
            if st.form_submit_button("注册语言", type="primary"):
                api("POST", "/languages/", json={"name": name, "file_ext": ext, "compile_cmd": compile_cmd, "run_cmd": run_cmd, "time_limit": seconds, "memory_limit": memory})
                st.success("语言已注册。")


@st.fragment(run_every=1.5 if st.session_state.get("poll_ai", True) else None)
def ai_panel(tid):
    try:
        task = api("GET", f"/ai/problem-tasks/{tid}")
        active = task["status"] in {"pending", "running"}
        if not active and st.session_state.get("poll_ai", True):
            st.session_state.poll_ai = False
            st.rerun()
        state_label = {"pending": "等待中", "running": "处理中", "success": "已完成", "cancelled": "已中断", "error": "失败"}
        st.subheader(state_label[task["status"]])
        st.caption("任务编号：" + tid)
        st.write(task["progress"])
        if active and st.button("中断任务", key="cancel_" + tid):
            api("PUT", f"/ai/problem-tasks/{tid}/cancel")
            st.rerun()
        usage = task["usage"]
        a, b, c = st.columns(3)
        a.metric("输入 Token", f"{usage['input_tokens']:,}")
        b.metric("输出 Token", f"{usage['output_tokens']:,}")
        c.metric("费用 · " + usage["currency"], f"{usage['cost']:.6f}")
        st.caption(f"{usage['basis']}。每 {usage['price_unit']:,} Token：输入 {usage['input_price']}，输出 {usage['output_price']} {usage['currency']}。")
        with st.expander("处理过程"):
            for event in task["events"]:
                st.write("· " + event)
        if task["status"] == "success":
            result = task["result"]
            st.success("参考解答已通过全部样例和测试点。")
            st.subheader(result["problem"]["title"])
            st.markdown(result["explanation"])
            with st.expander("测试覆盖与参考解答"):
                st.markdown("\n".join(f"- {item}" for item in result["coverage"]))
                st.code(result["reference_solution"], language=result.get("reference_language", "python"))
            if result["validation"].get("independent_passed"):
                with st.expander("输入校验与独立解答"):
                    st.write(result["validation"]["verification_explanation"])
                    st.caption(f"交叉核对后修正了 {result['validation']['corrected_outputs']} 处标准输出。")
                    st.code(result["validation"]["input_validator"], language="python")
                    st.code(result["validation"]["oracle_solution"], language="python")
            st.caption(result["validation"]["note"])
            if st.button("送入题目编辑器", type="primary", key="apply_" + tid):
                st.session_state.ai_draft = result["problem"]
                st.session_state.draft_version = st.session_state.get("draft_version", 0) + 1
                go("题目管理")
        elif task["status"] == "error":
            st.error("本次未生成可保存的题目，请调整需求或模型配置后重试。")
        if task["status"] in {"error", "cancelled"}:
            st.caption("重新开始将沿用本次命题要求，使用当前已保存的模型配置创建新任务，并重新统计用量。")
            if st.button("重新开始", type="primary", key="restart_" + tid):
                previous = st.session_state.get("ai_requests", {}).get(tid, {})
                with st.spinner("正在重新开始…"):
                    result = api("POST", "/ai/problem-tasks/", json={
                        "requirement": task["requirement"],
                        "problem_id": task.get("problem_id", previous.get("problem_id")),
                    })
                st.session_state.ai_requests = {result["task_id"]: {
                    "problem_id": task.get("problem_id", previous.get("problem_id"))}}
                st.session_state.ai_task = result["task_id"]
                st.session_state.ai_restart_notice = "已创建新的命题任务：" + result["task_id"]
                st.session_state.poll_ai = True
                st.rerun()
    except APIError as exc:
        st.error(str(exc))


def ai_page():
    heading("AI 智能命题", "描述教学目标，生成题面与测试点，再送入编辑器审阅。")
    if st.session_state.get("ai_restart_notice"):
        st.success(st.session_state.ai_restart_notice)
    config = api("GET", "/ai/model-config") or {}
    with st.expander("模型配置", expanded=not bool(config)):
        with st.form("model_config"):
            url = st.text_input("提供商 URL", value=config.get("provider_url", ""), placeholder="https://provider.example/v1")
            st.caption("填写兼容 Chat Completions 流式协议的服务地址，包含 /v1，不包含 /chat/completions。")
            model = st.text_input("模型名称", value=config.get("model", ""))
            key = st.text_input("模型密钥", type="password", placeholder="已保存密钥；更新配置时请重新输入" if config else "输入 API Key")
            a, b, c, d = st.columns(4)
            incoming = a.number_input("输入单价", min_value=0.0, value=float(config.get("input_price", 0)), format="%.4f")
            outgoing = b.number_input("输出单价", min_value=0.0, value=float(config.get("output_price", 0)), format="%.4f")
            unit = c.number_input("计价 Token 数", min_value=1, value=config.get("price_unit", 1000000))
            currency = d.selectbox("币种", ["USD", "CNY"], index=1 if config.get("currency") == "CNY" else 0)
            st.caption("单价请按提供商实际报价填写；设置为 0 时统计费用为 0。密钥仅以加密形式保存在后端。")
            if st.form_submit_button("保存模型配置", type="primary"):
                api("PUT", "/ai/model-config", json={"provider_url": url, "model": model, "api_key": key, "input_price": incoming, "output_price": outgoing, "price_unit": unit, "currency": currency})
                st.success("配置已保存。")
    with st.form("ai_requirement"):
        requirement = st.text_area("命题需求", height=160, placeholder="例如：面向刚学完循环与列表的同学，设计一道入门题。要求覆盖负数、重复元素和空结果；明确输入规模，并包含能区分 O(n) 与 O(n²) 解法的数据。")
        pid = st.text_input("参考或修改已有题目（可选）", placeholder="填写题目编号")
        if st.form_submit_button("开始命题", type="primary"):
            result = api("POST", "/ai/problem-tasks/", json={"requirement": requirement, "problem_id": pid or None})
            st.session_state.ai_task = result["task_id"]
            st.session_state.ai_requests = {result["task_id"]: {"problem_id": pid or None}}
            st.session_state.poll_ai = True
            st.rerun()
    if st.session_state.get("ai_task"):
        ai_panel(st.session_state.ai_task)


def account_page():
    heading("我的账户", "查看账户信息与练习进度。")
    user = api("GET", "/auth/me")
    st.subheader(user["username"])
    st.caption(f"用户编号 {user['user_id']}　 /　角色 {user['role']}　 /　加入时间 {user['join_time']}")
    a, b = st.columns(2)
    a.metric("累计提交", user["submit_count"])
    b.metric("通过题目", user["resolve_count"])


def users_page():
    heading("用户管理", "查询账户，并管理普通用户、管理员和禁用状态。")
    a, b, c = st.columns([2, 1, 1])
    search = a.text_input("用户名筛选")
    role = b.selectbox("角色筛选", ["全部", "user", "admin", "banned"])
    page = c.number_input("页码", min_value=1, step=1)
    result = api("GET", "/users/", params={"username": search, "role": "" if role == "全部" else role, "page": page, "page_size": 20})
    st.caption(f"共 {result['total']} 位用户")
    st.dataframe(result["users"], hide_index=True, use_container_width=True)
    if result["users"]:
        with st.form("role_change"):
            uid = st.selectbox("用户", [u["user_id"] for u in result["users"]], format_func=lambda x: next(u["username"] + " · " + x for u in result["users"] if u["user_id"] == x))
            role = st.selectbox("新角色", ["user", "admin", "banned"])
            if st.form_submit_button("更新角色", type="primary"):
                api("PUT", f"/users/{uid}/role", json={"role": role})
                st.rerun()
    with st.expander("创建管理员"):
        with st.form("new_admin"):
            name = st.text_input("用户名", key="admin_name")
            password = st.text_input("密码", type="password", key="admin_password")
            if st.form_submit_button("创建管理员账户"):
                api("POST", "/users/admin", json={"username": name, "password": password})
                st.success("管理员账户已创建。")


def audit_page():
    heading("日志审计", "查看评测日志访问记录与用户权限变更。")
    access, roles = st.tabs(["日志访问", "权限变更"])
    with access:
        a, b, c = st.columns(3)
        uid = a.text_input("用户编号", key="audit_uid")
        pid = b.text_input("题目编号", key="audit_pid")
        page = c.number_input("页码", min_value=1, key="audit_page")
        st.dataframe(api("GET", "/logs/access/", params={"user_id": uid, "problem_id": pid, "page": page, "page_size": 30}), hide_index=True, use_container_width=True)
    with roles:
        st.dataframe(api("GET", "/logs/roles/"), hide_index=True, use_container_width=True)


def run_app():
    sync_browser_cookie(api)
    if not st.session_state.get("auth_restore_attempted"):
        st.session_state.auth_restore_attempted = True
        token = st.context.cookies.get("session_id")
        if isinstance(token, str) and token:
            base_url = os.getenv("OJ_API_URL", "http://127.0.0.1:8001")
            st.session_state.client = httpx.Client(base_url=base_url, timeout=20, follow_redirects=True,
                                                  trust_env=False, cookies={"session_id": token})
            try:
                st.session_state.user = api("GET", "/auth/me")
            except APIError as exc:
                if exc.code not in {401, 403}:
                    st.session_state.auth_restore_attempted = False
                    raise
                st.session_state.client.cookies.clear()
                st.session_state.browser_cookie_pending = {"ticket": None, "id": uuid.uuid4().hex}
    with st.sidebar:
        st.markdown('<div class="sidebar-brand"><div class="sidebar-mark">ZX</div><div><div class="sidebar-brand-name">知行 OJ</div><div class="sidebar-brand-subtitle">程序设计训练</div></div></div>', unsafe_allow_html=True)
        st.divider()
    if "user" not in st.session_state:
        with st.sidebar:
            st.markdown('<div class="sidebar-section-label">学习空间</div>', unsafe_allow_html=True)
            st.caption("在线评测 · 题目管理 · AI 命题")
        authentication_page()
        return
    # Revalidate the role with the backend on every full rerun.
    st.session_state.user = api("GET", "/auth/me")
    pages = {"题库": problems_page, "提交记录": submissions_page, "题目管理": problem_management, "AI 智能命题": ai_page, "我的账户": account_page}
    if st.session_state.user["role"] == "admin":
        pages.update({"用户管理": users_page, "日志审计": audit_page})
    if "next_page" in st.session_state:
        st.session_state.nav = st.session_state.pop("next_page")
    if st.session_state.get("nav") not in pages:
        st.session_state.nav = "题库"
    with st.sidebar:
        st.markdown('<div class="sidebar-section-label">工作台</div>', unsafe_allow_html=True)
        for index, page in enumerate(pages, 1):
            if st.button(f"{index:02d}  {page}", key=f"workbench_{page}", type="primary" if page == st.session_state.nav else "secondary", use_container_width=True):
                st.session_state.nav = page
                st.rerun()
        selected = st.radio("导航", list(pages), key="nav", label_visibility="collapsed", help="工作台导航状态")
        st.divider()
        role_label = "管理员" if st.session_state.user["role"] == "admin" else "普通用户"
        username = html.escape(st.session_state.user["username"])
        st.markdown(f'<div class="sidebar-account"><div class="sidebar-account-name">{username}</div><div class="sidebar-account-role">{role_label}</div></div>', unsafe_allow_html=True)
        if st.button("退出登录", use_container_width=True):
            api("POST", "/auth/logout")
            st.session_state.client.close()
            st.session_state.clear()
            st.session_state.auth_restore_attempted = True
            st.session_state.browser_cookie_pending = {"ticket": None, "id": uuid.uuid4().hex}
            st.rerun()
        st.caption("知行 OJ · 课程实验")
    pages[selected]()


try:
    run_app()
except APIError as exc:
    st.error(f"{exc}（{exc.code}）")
    if exc.code in {401, 403} and "user" in st.session_state:
        if st.button("重新登录"):
            st.session_state.client.close()
            st.session_state.clear()
            st.session_state.auth_restore_attempted = True
            st.session_state.browser_cookie_pending = {"ticket": None, "id": uuid.uuid4().hex}
            st.rerun()
