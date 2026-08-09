import sys
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add current dir to path to import backend modules
sys.path.append(os.path.abspath('.'))

from core.database import SessionLocal, engine
from nexus.models_phase3 import NexusCampaign, NexusLead, NexusGlobalLead
from nexus.models import NexusWorkspace
from nexus.models_phase2 import NexusProduct
from models import User

db = SessionLocal()
try:
    print("--- WORKSPACES ---")
    workspaces = db.query(NexusWorkspace).all()
    for w in workspaces:
        print(f"ID: {w.id}, Name: {w.name}")

    print("\n--- PRODUCTS ---")
    products = db.query(NexusProduct).all()
    for p in products:
        print(f"ID: {p.id}, Name: {p.name}, URL: {p.source_url}, ICP: {p.icp}")

    print("\n--- CAMPAIGNS ---")
    campaigns = db.query(NexusCampaign).all()
    for c in campaigns:
        lead_count = db.query(NexusLead).filter(NexusLead.campaign_id == c.id).count()
        print(f"ID: {c.id}, Name: {c.name}, Status: {c.status}, ProductID: {c.product_id}, LeadCount: {lead_count}, ICP: {c.icp}")

    print("\n--- LEADS ---")
    leads = db.query(NexusLead).all()
    print(f"Total Leads: {len(leads)}")
    for l in leads[:10]:
        gl = l.global_lead
        print(f"Lead ID: {l.id}, Score: {l.icp_score}, Email: {gl.email if gl else 'N/A'}, Company: {gl.company_name if gl else 'N/A'}, Signals: {l.signals}")

finally:
    db.close()
