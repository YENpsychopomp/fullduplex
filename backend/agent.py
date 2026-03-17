import os
import time
import json
import dotenv
import traceback
import uvicorn as uv
import pandas as pd
from typing import List, Dict, Any
from decimal import Decimal
from fastapi import FastAPI, Request, Header
from fastapi.responses import StreamingResponse
# from db import connect_db, check_and_reset_user, deduct_quota
# LangChain
from langchain_openai import AzureChatOpenAI
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, AIMessage

llm = AzureChatOpenAI(
    azure_deployment=dotenv.get_key(".env", "AZURE_OPENAI_CHAT_DEPLOYMENT_NAME"),
    temperature=0.7,
    max_tokens=None,
    timeout=None,
    max_retries=2,
    azure_endpoint=dotenv.get_key(".env", "AZURE_OPENAI_ENDPOINT"),
    api_version=dotenv.get_key(".env", "OPENAI_API_VERSION"),
    api_key=dotenv.get_key(".env", "AZURE_OPENAI_API_KEY"),
    streaming=True,
)
