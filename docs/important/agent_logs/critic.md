# CRITIC Agent Logs

## Agent Name
**CRITIC** (The Strategic Reviewer)

## Purpose
Final verification of the entire campaign payload for consistency, branding guidelines, and strategic impact.

## Prompt Template
```python
prompt = f"""
You are the Senior Strategic Critic for NEUZEN AI.
Your job is to verify the entire generated campaign payload for consistency, branding, and impact.

ORIGINAL BRIEF: "{campaign_brief}"
GENERATED PAYLOAD: {json.dumps(final_payload, indent=2)}

VERIFICATION CHECKLIST:
1. Does the content align with the original brief?
2. Are the branding rules (Bold Unicode, tone) applied?
3. Are the visuals (generation prompts) distinct and platform-optimized?
4. Are there any critical errors or inconsistencies?

Return STRICTLY in this JSON format:
{{
    "is_valid": true | false,
    "critique": "Overall strategic assessment...",
    "adjustments": "Specific tiny fixes if needed (otherwise empty string)"
}}
"""
```

## Actual Full Prompt (Raw)
```text
(Full Prompt Sent to Gemini - Includes the entire assembled campaign)
You are the Senior Strategic Critic for NEUZEN AI.
Your job is to verify the entire generated campaign payload for consistency, branding, and impact.

ORIGINAL BRIEF: "Spenzo is an AI-powered marketing intelligence and optimization platform designed for modern growth teams to measure performance, forecast outcomes, and maximize ROI across channels. It connects seamlessly with platforms like Amazon Web Services, Snowflake, Google Ads, Meta Ads, and TikTok to unify data into a single intelligent layer. Powered by AI agents, Spenzo enables automated insights, MMM-based forecasting, and budget optimization, helping teams achieve higher ROAS, eliminate manual analysis, and make faster data-driven decisions. The campaign should highlight measurable impact (e.g., 3.2x ROAS, 92% model accuracy), intelligent automation, and ease of use through conversational AI. Visuals should focus on connected marketing stacks, performance dashboards, and budget allocation scenarios. The target audience includes marketing leaders, performance marketers, and data teams managing large-scale ad spend. The tone should be sharp, results-driven, and analytical, with a strong CTA to book a demo and optimize marketing performance. https://spenzo.io/"

GENERATED PAYLOAD: {
  "refined_brief": "## Campaign Brief: Spenzo - Precision Performance, Unlocked.\n\n### **1. Campaign Objective:**...",
  "research_report": {
    "target_audience": "Marketing leaders, performance marketers, and data teams...",
    "trending_context": "The current marketing landscape is characterized by data fragmentation...",
    "problem_solving_opportunity": "Modern growth teams are challenged by fragmented marketing data...",
    "company_product_analysis": "Spenzo is an AI-powered marketing intelligence and optimization platform..."
  },
  "content": {
    "linkedin": {
      "viral_reach": "Is your marketing budget bleeding ROI due to fragmented data?...",
      "high_interaction": "Marketing leaders & data teams: What's the biggest roadblock?...",
      "follower_growth": "Elevate your marketing performance with AI..."
    },
    ... (Rest of Twitter, Instagram, Facebook content) ...
  },
  "visuals": [
    {
      "url": "https://...",
      "name": "Global Strategist",
      "generation_prompt": "STRICT SQUARE 1:1 RATIO. RESOLUTION 1024x1024..."
    },
    ...
  ]
}

VERIFICATION CHECKLIST:
1. Does the content align with the original brief?
2. Are the branding rules (Bold Unicode, tone) applied?
3. Are the visuals (generation prompts) distinct and platform-optimized?
4. Are there any critical errors or inconsistencies?

Return STRICTLY in this JSON format:
...
```

## Actual Response (Raw JSON)
```json
{
    "is_valid": false,
    "critique": "The generated campaign payload demonstrates strong alignment with the original brief's core objectives... However, there is a critical and consistent branding inconsistency... The generated content repeatedly includes 'by NeuZenAI' and directs users to 'Follow NeuZenAI'... This self-promotional inclusion is a fundamental strategic error. It dilutes Spenzo's brand focus.",
    "adjustments": "1. Remove all instances of 'by NeuZenAI' from all content variants. 2. Change 'At NeuZenAI, we believe...' to 'The future of growth is...'. 3. Replace 'Follow NeuZenAI for cutting-edge...' with 'Stay ahead of the curve in AI-powered optimization.'."
}
```
