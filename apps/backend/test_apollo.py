import sys
import os
import asyncio

sys.path.append(os.path.abspath('.'))

from core.database import SessionLocal
from nexus.models_phase3 import NexusCampaign
from nexus.services.discovery_apollo import search_people, _icp_to_apollo_body, _api_key

async def test():
    db = SessionLocal()
    try:
        # Get one of the campaigns with 0 leads, e.g. campaign 23
        campaign = db.query(NexusCampaign).filter(NexusCampaign.id == 23).first()
        if not campaign:
            print("Campaign 23 not found")
            return
        
        print("Campaign Name:", campaign.name)
        print("Campaign ICP:", campaign.icp)
        
        key = _api_key()
        print("Apollo API Key:", key)
        
        # Test 1: Generate Apollo body
        body = _icp_to_apollo_body(campaign.icp, page=1, per_page=10)
        print("\nGenerated Apollo Request Body:\n", body)
        
        # Test 2: Execute search_people
        print("\nExecuting search_people...")
        results = await search_people(db, campaign.icp, max_leads=10)
        print(f"Returned {len(results)} leads")
        for r in results:
            print(f"  - {r.get('first_name')} {r.get('last_name')} ({r.get('email')}) at {r.get('company_name')}")
            
        # Test 3: Run without Spenzo keyword
        print("\n--- Test 3: Run search with Spenzo and Zyntegrate removed from keywords ---")
        clean_icp = dict(campaign.icp)
        if "tech_stack_hints" in clean_icp:
            clean_icp["tech_stack_hints"] = [x for x in clean_icp["tech_stack_hints"] if x.lower() not in ("spenzo", "zyntegrate")]
        print("Cleaned ICP tech_stack_hints:", clean_icp.get("tech_stack_hints"))
        clean_body = _icp_to_apollo_body(clean_icp, page=1, per_page=10)
        print("Cleaned Apollo Request Body:\n", clean_body)
        
        # Clear cache first by deleting from db or bypassing it
        # Since discovery_apollo uses query_hash, let's just run it
        results_clean = await search_people(db, clean_icp, max_leads=10)
        print(f"Returned {len(results_clean)} leads with cleaned keywords!")
        for r in results_clean[:5]:
            print(f"  - {r.get('first_name')} {r.get('last_name')} ({r.get('email')}) at {r.get('company_name')}")
            
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(test())
