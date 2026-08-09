# RESEARCHER Agent Logs

## Agent Name
**RESEARCHER** (The Insight Agent)

## Purpose
Deep research into company/product, target audience, trends, and problem-solving opportunities based on the refined brief.

## Prompt Template
```python
prompt = f"""
You are a Deep Research AI. Analyze this refined brief: "{refined_brief}"

TASK:
1. Identify any specific Company, Brand, or Product name mentioned (e.g., NEUZEN AI, SWASS, Pipelyt).
2. Define a crystal clear Target Audience.
3. Analyze the current Trending Context or tech landscape.
4. Highlight the specific Problem-Solving Opportunity.
5. Provide a specific analysis of the "Company/Product" value proposition if identified, otherwise provide a general industry-leader perspective.

Return STRICTLY in this JSON format (ALL VALUES MUST BE STRINGS, NO NESTED OBJECTS):
{{
    "target_audience": "...",
    "trending_context": "...",
    "problem_solving_opportunity": "...",
    "company_product_analysis": "..."
}}
"""
```

## Actual Full Prompt (Raw)
```text
You are a Deep Research AI. Analyze this refined brief: "Spenzo is an AI-powered marketing intelligence and optimization platform designed for modern growth teams to measure performance, forecast outcomes, and maximize ROI across channels. It connects seamlessly with platforms like Amazon Web Services, Snowflake, Google Ads, Meta Ads, and TikTok to unify data into a single intelligent layer. Powered by AI agents, Spenzo enables automated insights, MMM-based forecasting, and budget optimization, helping teams achieve higher ROAS, eliminate manual analysis, and make faster data-driven decisions. The campaign should highlight measurable impact (e.g., 3.2x ROAS, 92% model accuracy), intelligent automation, and ease of use through conversational AI. Visuals should focus on connected marketing stacks, performance dashboards, and budget allocation scenarios. The target audience includes marketing leaders, performance marketers, and data teams managing large-scale ad spend. The tone should be sharp, results-driven, and analytical, with a strong CTA to book a demo and optimize marketing performance. https://spenzo.io/"

TASK:
1. Identify any specific Company, Brand, or Product name mentioned (e.g., NEUZEN AI, SWASS, Pipelyt).
2. Define a crystal clear Target Audience.
3. Analyze the current Trending Context or tech landscape.
4. Highlight the specific Problem-Solving Opportunity.
5. Provide a specific analysis of the "Company/Product" value proposition if identified, otherwise provide a general industry-leader perspective.

Return STRICTLY in this JSON format (ALL VALUES MUST BE STRINGS, NO NESTED OBJECTS):
{
    "target_audience": "...",
    "trending_context": "...",
    "problem_solving_opportunity": "...",
    "company_product_analysis": "..."
}
```

## Actual Response (Raw JSON)
```json
{
    "target_audience": "Marketing leaders, performance marketers, and data teams managing large-scale ad spend.",
    "trending_context": "The current marketing landscape is characterized by data fragmentation across numerous ad platforms (Google Ads, Meta Ads, TikTok), cloud data warehouses (Snowflake, AWS), and other analytics tools. There is a growing demand for AI/ML-driven automation to overcome manual analysis limitations, enable predictive forecasting (especially via Media Mix Modeling - MMM), and optimize budget allocation for maximum ROAS. The trend is towards unified data intelligence, actionable automated insights, and conversational AI interfaces to simplify complex data interactions and accelerate decision-making in a multi-channel environment.",
    "problem_solving_opportunity": "Modern growth teams are challenged by fragmented marketing data, making it difficult to accurately measure performance, forecast outcomes, and optimize ROI across diverse channels. Manual analysis is time-consuming and prone to human error, leading to suboptimal budget allocation and slow decision-making. There is a significant opportunity to provide a platform that seamlessly unifies disparate data sources, automates complex analytical processes through AI, offers predictive capabilities like MMM-based forecasting, and provides clear, actionable budget optimization recommendations to achieve higher ROAS and eliminate the bottlenecks of traditional marketing intelligence.",
    "company_product_analysis": "Spenzo is an AI-powered marketing intelligence and optimization platform that directly addresses the challenges of data fragmentation and manual analysis. Its core value proposition is the unification of data from major ad platforms and data warehouses (e.g., Google Ads, Meta Ads, TikTok, AWS, Snowflake) into a single intelligent layer. Leveraging AI agents, Spenzo provides automated insights, performs MMM-based forecasting, and optimizes budgets to achieve higher ROAS and streamline decision-making. Key differentiators include measurable impact (e.g., 3.2x ROAS, 92% model accuracy), intelligent automation, and ease of use through conversational AI, positioning it as an essential tool for marketing leaders and data teams seeking to maximize ad spend efficiency and accelerate growth."
}
```
