# COPYWRITER Agent Logs

## Agent Name
**COPYWRITER** (The Content Creator)

## Purpose
Strategic content creation across multiple platforms (LinkedIn, Twitter, Instagram, Facebook), utilizing platform-specific brand voice and strategic variants.

## Prompt Template
```python
prompt = f"""
You are the content creator for the following company/user profile:
{user_context}

Create 3 content variants for EACH of these platforms: {platforms_str} based on our brand voice and the brief below.

BRIEF: "{refined_brief}"
RESEARCH: {json.dumps(research)}

STYLE GUIDELINES:
1. TONE: Professional and authoritative. Use high-impact hooks.
3. PLATFORM CHARACTER LIMITS & FORMATTING:
   - Twitter: STRICTLY UNDER 280 CHARACTERS. Standard text only (no bold Unicode).
   - LinkedIn: Under 3,000 characters. Use professional, value-driven tone.
   - Instagram: Under 2,200 characters. Use engaging, catchy hooks and emojis.
   - Facebook: Under 5,000 characters for high readability.

4. BRANDING RULES:
   - Use Bold Unicode for brand names (e.g., \ud835\udde1\ud835\uddd8\ud835\udde8\ud835\udded\ud835\uddd8\ud835\udde1 \ud835\uddd4\ud835\udddc, \ud835\udde6\ud835\uddea\ud835\uddd4\ud835\udde6\ud835\udde6 \ud835\uddd4\ud835\udddc, \ud835\udddf\ud835\uddf2\ud835\uddfb\ud835\ude00\ud835\uddd4\ud835\udddc) ONLY on LinkedIn, Facebook, and Instagram.
   - Use arrow bullets (\u21b3) for key points.
   - Use strategic line breaks for a premium feel.

5. STRATEGIC VARIANTS & GOALS (MANDATORY):
- "viral_reach": Broad hooks, high-sharing potential, trending perspective. Goal: Maximum Platform Reach.
- "high_interaction": Discussion starters, "Tag a colleague", Poll-style questions. Goal: Maximize Likes & Comments.
- "follower_growth": Value authority, "Follow for [Value]", Relationship-building CTAs. Goal: Increase Followers.

6. ENGAGEMENT PROTOCOL:
Every variant MUST end with a high-impact engagement anchor (CTA) tailored to the specific variant goal (e.g., "Join the conversation below \ud83d\udc47", "Follow for more AI insights \ud83d\udd14").

Return STRICTLY in this JSON format:
...
"""
```

## Actual Full Prompt (Raw)
```text
(Full Prompt Sent to Gemini)
You are the content creator for the following company/user profile:

    Company: NeuZenAI
    Description: N/A
    Products/Products Details: N/A
    Target Tone: Professional & Impactful
    User Timezone: Asia/Kolkata


Create 3 content variants for EACH of these platforms: linkedin, twitter, instagram, facebook based on our brand voice and the brief below.

BRIEF: "Spenzo is an AI-powered marketing intelligence and optimization platform designed for modern growth teams to measure performance, forecast outcomes, and maximize ROI across channels. It connects seamlessly with platforms like Amazon Web Services, Snowflake, Google Ads, Meta Ads, and TikTok to unify data into a single intelligent layer. Powered by AI agents, Spenzo enables automated insights, MMM-based forecasting, and budget optimization, helping teams achieve higher ROAS, eliminate manual analysis, and make faster data-driven decisions. The campaign should highlight measurable impact (e.g., 3.2x ROAS, 92% model accuracy), intelligent automation, and ease of use through conversational AI. Visuals should focus on connected marketing stacks, performance dashboards, and budget allocation scenarios. The target audience includes marketing leaders, performance marketers, and data teams managing large-scale ad spend. The tone should be sharp, results-driven, and analytical, with a strong CTA to book a demo and optimize marketing performance. https://spenzo.io/"
RESEARCH: {"target_audience": "Marketing leaders, performance marketers, and data teams managing large-scale ad spend.", "trending_context": "The current marketing landscape is characterized by data fragmentation across numerous ad platforms (Google Ads, Meta Ads, TikTok), cloud data warehouses (Snowflake, AWS), and other analytics tools. There is a growing demand for AI/ML-driven automation to overcome manual analysis limitations, enable predictive forecasting (especially via Media Mix Modeling - MMM), and optimize budget allocation for maximum ROAS. The trend is towards unified data intelligence, actionable automated insights, and conversational AI interfaces to simplify complex data interactions and accelerate decision-making in a multi-channel environment.", "problem_solving_opportunity": "Modern growth teams are challenged by fragmented marketing data, making it difficult to accurately measure performance, forecast outcomes, and optimize ROI across diverse channels. Manual analysis is time-consuming and prone to human error, leading to suboptimal budget allocation and slow decision-making. There is a significant opportunity to provide a platform that seamlessly unifies disparate data sources, automates complex analytical processes through AI, offers predictive capabilities like MMM-based forecasting, and provides clear, actionable budget optimization recommendations to achieve higher ROAS and eliminate the bottlenecks of traditional marketing intelligence.", "company_product_analysis": "Spenzo is an AI-powered marketing intelligence and optimization platform that directly addresses the challenges of data fragmentation and manual analysis. Its core value proposition is the unification of data from major ad platforms and data warehouses (e.g., Google Ads, Meta Ads, TikTok, AWS, Snowflake) into a single intelligent layer. Leveraging AI agents, Spenzo provides automated insights, performs MMM-based forecasting, and optimizes budgets to achieve higher ROAS and streamline decision-making. Key differentiators include measurable impact (e.g., 3.2x ROAS, 92% model accuracy), intelligent automation, and ease of use through conversational AI, positioning it as an essential tool for marketing leaders and data teams seeking to maximize ad spend efficiency and accelerate growth."}

STYLE GUIDELINES:
1. TONE: Professional and authoritative. Use high-impact hooks.
3. PLATFORM CHARACTER LIMITS & FORMATTING:
   - Twitter: STRICTLY UNDER 280 CHARACTERS. Standard text only (no bold Unicode).
   - LinkedIn: Under 3,000 characters. Use professional, value-driven tone.
   - Instagram: Under 2,200 characters. Use engaging, catchy hooks and emojis.
   - Facebook: Under 5,000 characters for high readability.

4. BRANDING RULES:
   - Use Bold Unicode for brand names (e.g., \ud835\udde1\ud835\uddd8\ud835\udde8\ud835\udded\ud835\uddd8\ud835\udde1 \ud835\uddd4\ud835\udddc, \ud835\udde6\ud835\uddea\ud835\uddd4\ud835\udde6\ud835\udde6 \ud835\uddd4\ud835\udddc, \ud835\udddf\ud835\uddf2\ud835\uddfb\ud835\ude00\ud835\uddd4\ud835\udddc) ONLY on LinkedIn, Facebook, and Instagram.
   - Use arrow bullets (\u21b3) for key points.
   - Use strategic line breaks for a premium feel.

5. STRATEGIC VARIANTS & GOALS (MANDATORY):
- "viral_reach": Broad hooks, high-sharing potential, trending perspective. Goal: Maximum Platform Reach.
- "high_interaction": Discussion starters, "Tag a colleague", Poll-style questions. Goal: Maximize Likes & Comments.
- "follower_growth": Value authority, "Follow for [Value]", Relationship-building CTAs. Goal: Increase Followers.

6. ENGAGEMENT PROTOCOL:
Every variant MUST end with a high-impact engagement anchor (CTA) tailored to the specific variant goal (e.g., "Join the conversation below \ud83d\udc47", "Follow for more AI insights \ud83d\udd14").

Return STRICTLY in this JSON format (DO NOT ommit any platform listed in linkedin, twitter, instagram, facebook):
{
    "recommendation": { "best_variant": "viral_reach/high_interaction/follower_growth", "reason": "..." },
    "content": {
        "platform_name": { 
            "viral_reach": "platform specific reach content...", 
            "high_interaction": "platform specific interaction content...", 
            "follower_growth": "platform specific growth variant..." 
        }
    }
}
```

## Actual Response (Raw JSON)
```json
{
    "recommendation": {
        "best_variant": "viral_reach",
        "reason": "The 'viral_reach' variants across platforms are designed with high-impact hooks and clear problem/solution statements that resonate broadly with the target audience's common pain points (data fragmentation, suboptimal ROAS). They strongly emphasize measurable results (e.g., 3.2x ROAS, 92% model accuracy), making them highly shareable and effective for maximizing initial exposure and driving interest in Spenzo's capabilities. The direct link to a demo combined with an engaging call to action encourages immediate exploration by a wide audience."
    },
    "content": {
        "linkedin": {
            "viral_reach": "Is your marketing budget bleeding ROI due to fragmented data? It's time to demand more than just metrics \u2013 demand intelligence.\n\nIntroducing \ud835\udde6\ud835\uddfd\ud835\uddf2\ud835\uddfb\ud835\ude07\ud835\uddfc by \ud835\udde1\ud835\uddf2\ud835\ude02\ud835\udded\ud835\uddf2\ud835\uddfb\ud835\uddd4\ud835\udddc, the AI-powered platform transforming how modern growth teams operate.\n\n\u21b3 Unify your entire marketing stack: AWS, Snowflake, Google Ads, Meta Ads, TikTok & more, into one intelligent layer.\n\u21b3 Achieve unprecedented accuracy: Our MMM-based forecasting boasts 92% model accuracy.\n\u21b3 Unlock massive growth: Drive up to 3.2x higher ROAS through automated budget optimization and insights.\n\nEliminate manual analysis and accelerate data-driven decisions with conversational AI. Stop guessing, start growing.\n\nReady to see your marketing spend truly perform? Discover \ud835\udde6\ud835\uddfd\ud835\uddf2\ud835\uddfb\ud835\ude07\ud835\uddfc:\nhttps://spenzo.io/\n\nShare your thoughts on marketing ROI below! \ud83d\udc47",
            "high_interaction": "Marketing leaders & data teams: What's the biggest roadblock to maximizing your campaign ROI right now? Is it fragmented data, slow insights, or inaccurate forecasting?\n\nImagine a world where your entire marketing stack \u2013 from Google Ads to Snowflake \u2013 speaks one language. That's the power of \ud835\udde6\ud835\uddfd\ud835\uddf2\ud835\uddfb\ud835\ude07\ud835\uddfc.\n\n\u21b3 Seamlessly connects all your platforms for a unified data view.\n\u21b3 AI agents deliver automated, actionable insights, eliminating manual grunt work.\n\u21b3 Conversational AI makes complex data accessible, enabling faster decisions.\n\nWe're helping teams achieve 3.2x higher ROAS and 92% model accuracy with ease.\n\nTag a colleague who needs to see this, or tell us: What's your biggest data challenge? Share below! \ud83d\udc47",
            "follower_growth": "Elevate your marketing performance with AI that truly understands your data. \n\nAt \ud835\udde1\ud835\uddf2\ud835\ude02\ud835\udded\ud835\uddf2\ud835\uddfb\ud835\uddd4\ud835\udddc, we believe the future of growth is intelligent, automated, and predictive. That's why we built \ud835\udde6\ud835\uddfd\ud835\uddf2\ud835\uddfb\ud835\ude07\ud835\uddfc.\n\n\u21b3 Gain automated insights and MMM-based forecasting with 92% accuracy.\n\u21b3 Optimize budgets across channels for higher ROAS (e.g., 3.2x).\n\u21b3 Unify data from AWS, Google Ads, Meta Ads, TikTok & more, effortlessly.\n\nFollow \ud835\udde1\ud835\uddf2\ud835\ude02\ud835\udded\ud835\uddf2\ud835\uddfb\ud835\uddd4\ud835\udddc for cutting-edge marketing intelligence that empowers data teams and marketing leaders to make smarter, faster decisions. Stay ahead of the curve in AI-powered optimization. \n\nFollow \ud835\udde1\ud835\uddf2\ud835\ude02\ud835\udded\ud835\uddf2\ud835\uddfb\ud835\uddd4\ud835\udddc for more AI insights \ud83d\udd14"
        },
        "twitter": {
            "viral_reach": "Stop guessing your marketing ROI. Spenzo's AI unifies data from Google Ads, Meta, TikTok & more.\n\nAchieve 3.2x higher ROAS & 92% forecast accuracy with automated insights. Eliminate manual analysis. Make faster, data-driven decisions.\n\nDemystify your spend: https://spenzo.io/\n\nRetweet if you're ready to amplify your ROAS! \ud83d\ude80",
            "high_interaction": "Is fragmented marketing data costing you? \ud83e\udd2f\n\nSpenzo's AI-powered platform unifies Google Ads, Meta, Snowflake, AWS & TikTok data. Get 92% accurate MMM forecasting & budget optimization.\n\nCut manual work, boost ROAS. \n\nWhat's your biggest data hurdle? Reply & let us know! \ud83d\udc47",
            "follower_growth": "Unlock smarter marketing with AI. \ud83d\udca1\n\nSpenzo by NeuZenAI delivers automated insights, MMM-based forecasting, & budget optimization for 3.2x higher ROAS.\n\nUnify data, accelerate decisions. \n\nFollow @NeuZenAI for daily marketing intelligence upgrades! \ud83d\udcc8"
        },
        "instagram": {
            "viral_reach": "Are you leaving ROAS on the table? \ud83d\udcb8\nIt's time for AI-powered precision in your marketing.\n\nIntroducing \ud835\udde6\ud835\uddfd\ud835\uddf2\ud835\uddfb\ud835\ude07\ud835\uddfc by \ud835\udde1\ud835\uddf2\ud835\ude02\ud835\udded\ud835\uddf2\ud835\uddfb\ud835\uddd4\ud835\udddc \u2013 the ultimate platform for growth teams.\n\n\u21b3 Unify all your ad data (Google Ads, Meta, TikTok) & warehouses (AWS, Snowflake) \ud83e\udd1d\n\u21b3 Achieve 3.2x higher ROAS with automated budget optimization \ud83d\udcc8\n\u21b3 Get 92% accurate forecasts with AI-driven MMM \ud83d\udd2e\n\nStop the manual madness! Spenzo's conversational AI makes data-driven decisions effortless. Ready for measurable impact?\n\nLink in bio to book your demo today! \ud83d\udd17\n\nDouble-tap if you're ready for 3.2x ROAS! \u2764\ufe0f\u200d\ud83d\udd25",
            "high_interaction": "Tired of marketing data chaos? \ud83d\ude35\u200d\ud83d\udcab Your multiple platforms don't have to be a mess! \n\n\ud835\udde6\ud835\uddfd\ud835\uddf2\ud835\uddfb\ud835\ude07\ud835\uddfc simplifies everything. \n\nWe seamlessly connect Amazon Web Services, Snowflake, Google Ads, Meta Ads, and TikTok to create one intelligent data layer. \n\n\u21b3 Automated insights from AI agents \ud83e\udd16\n\u21b3 Precision forecasting with MMM \n\u21b3 Easy budget optimization with conversational AI \ud83d\udcac\n\nImagine higher ROAS and zero manual analysis. \n\nTag a friend who needs this data superpower! What's your biggest marketing data headache? Tell us below! \ud83d\udc47",
            "follower_growth": "Unlock the future of marketing intelligence. \u2728\n\n\ud835\udde6\ud835\uddfd\ud835\uddf2\ud835\uddfb\ud835\ude07\ud835\uddfc by \ud835\udde1\ud835\uddf2\ud835\ude02\ud835\udded\ud835\uddf2\ud835\uddfb\ud835\uddd4\ud835\udddc is built for modern growth teams to dominate their channels.\n\n\u21b3 Unify fragmented data for a holistic view.\n\u21b3 Drive higher ROAS with intelligent budget optimization.\n\u21b3 Make faster, smarter decisions with 92% accurate AI forecasts.\n\nWe provide the tools for unparalleled performance and efficiency. Elevate your strategy with cutting-edge AI.\n\nFollow \ud835\udde1\ud835\uddf2\ud835\ude02\ud835\udded\ud835\uddf2\ud835\uddfb\ud835\uddd4\ud835\udddc for your daily dose of AI marketing mastery! \ud83d\ude80"
        },
        "facebook": {
            "viral_reach": "Is your marketing spend delivering its true potential? In today's complex, multi-channel world, fragmented data is the enemy of ROI. \n\nMeet \ud835\udde6\ud835\uddfd\ud835\uddf2\ud835\uddfb\ud835\ude07\ud835\uddfc by \ud835\udde1\ud835\uddf2\ud835\ude02\ud835\udded\ud835\uddf2\ud835\uddfb\ud835\uddd4\ud835\udddc \u2013 your AI-powered solution for marketing intelligence and optimization.\n\nWe bridge the gap, unifying data from AWS, Snowflake, Google Ads, Meta Ads, TikTok, and more into a single, intelligent layer. No more manual spreadsheets or disjointed reports.\n\n\u21b3 Achieve unprecedented results: up to 3.2x higher ROAS through automated budget optimization.\n\u21b3 Gain predictive power: 92% model accuracy with MMM-based forecasting.\n\u21b3 Simplify insights: Conversational AI agents deliver clear, actionable recommendations.\n\nTransform your growth team's ability to measure, forecast, and maximize ROI across every channel. Stop reacting, start optimizing.\n\nReady for a marketing revolution? Book your \ud835\udde6\ud835\uddfd\ud835\uddf2\ud835\uddfb\ud835\ude07\ud835\uddfc demo today: https://spenzo.io/\n\nIs your team ready for a ROAS revolution? Share your thoughts! \ud83d\udc47",
            "high_interaction": "Marketing leaders and performance marketers: Let's talk real challenges. How do you currently manage data fragmentation across platforms like Google Ads, Meta, and TikTok, while also leveraging insights from your cloud data warehouses like AWS or Snowflake? \n\nIt's a complex landscape, but \ud835\udde6\ud835\uddfd\ud835\uddf2\ud835\uddfb\ud835\ude07\ud835\uddfc is designed to simplify it.\n\nWe connect your entire marketing stack seamlessly, providing a unified data layer that fuels AI agents for:\n\u21b3 Automated, actionable insights that cut through the noise.\n\u21b3 Accurate MMM-based forecasting to predict future outcomes.\n\u21b3 Intelligent budget optimization to ensure every dollar works harder.\n\nOur platform helps teams achieve measurable impact, like 3.2x ROAS and 92% model accuracy, all through an intuitive, conversational AI interface.\n\nTag team members who face these challenges daily, or tell us: How do you tackle data fragmentation? Join the conversation! \ud83d\udc47",
            "follower_growth": "Master the art of AI-driven marketing with \ud835\udde6\ud835\uddfd\ud835\uddf2\ud835\uddfb\ud835\ude07\ud835\uddfc by \ud835\udde1\ud835\uddf2\ud835\ude02\ud835\udded\ud835\uddf2\ud835\uddfb\ud835\uddd4\ud835\udddc. \n\nFor marketing leaders, performance marketers, and data teams managing significant ad spend, the path to higher ROAS and smarter decisions starts here. \n\n\ud835\udde6\ud835\uddfd\ud835\uddf2\ud835\uddfb\ud835\ude07\ud835\uddfc is not just a tool; it's your strategic partner:\n\u21b3 Unify all your critical marketing data from Amazon Web Services, Snowflake, Google Ads, Meta Ads, TikTok, and beyond.\n\u21b3 Leverage AI agents for automated insights and eliminate tedious manual analysis.\n\u21b3 Benefit from MMM-based forecasting with 92% accuracy and dynamic budget optimization.\n\nJoin our growing community of growth teams achieving remarkable results, including up to 3.2x higher ROAS.\n\nFollow \ud835\udde1\ud835\uddf2\ud835\ude02\ud835\udded\ud835\uddf2\ud835\uddfb\ud835\uddd4\ud835\udddc for ongoing strategies to master AI-powered marketing and stay ahead in the competitive digital landscape! \ud83d\udca1"
        }
    }
}
```
