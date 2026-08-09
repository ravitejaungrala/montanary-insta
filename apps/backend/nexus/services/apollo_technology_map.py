"""Gemini-output → Apollo technology-UID lookup.

Apollo's `/v1/mixed_people/api_search` `currently_using_any_of_technology_uids`
field expects Apollo's own technology UIDs (lower_snake_case, e.g.
"salesforce", "microsoft_teams", "amazon_web_services_aws") — NOT the
human-readable display names Gemini emits ("Salesforce", "Microsoft Teams").

# Why this file exists

Before this module, `_icp_to_apollo_body()` forwarded Gemini's free-form
technology strings VERBATIM to Apollo. Display names like "Salesforce"
(capitalised) don't match Apollo's UID "salesforce", so Apollo silently
no-op'd them — the wizard's TARGET TECHNOLOGIES chips had **no effect on
discovery** for known technologies. Same silent-failure mode that
apollo_industry_map.py fixed for industries.

`technologies_to_uids()` resolves a display name → UID via:
  1. exact match in APOLLO_TECHNOLOGY_UIDS, then
  2. case-insensitive match, then
  3. VERBATIM pass-through (capped) for anything unknown.

Step 3 is the key difference from the industry map: Apollo's technology
field is genuinely FREE-FORM (unknown UIDs are silently ignored), and the
wizard lets users type arbitrary tech. So we never DROP an unknown label —
we forward it as-is (exactly the pre-map behaviour), guaranteeing no
regression while upgrading the 400 known technologies to real UIDs.

# Safety contract
  - NEVER raises. Bad inputs → empty/best-effort output, never a crash.
  - Output is deduplicated and order-preserving.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable, List, Optional

logger = logging.getLogger("pipelyt.nexus.apollo_technology_map")


# ─────────────────────────────────────────────────────────────────────────────
# Apollo's top technologies — display name → Apollo technology UID.
# Source: Apollo's published technology taxonomy. Extend by appending lines.
# ─────────────────────────────────────────────────────────────────────────────
APOLLO_TECHNOLOGY_UIDS: dict[str, str] = {
    "3D cart": "3d_cart",
    "8x8": "8x8",
    "ABAP": "abap",
    "AC500 PLC": "ac500_plc",
    "Act-On": "act-on",
    "Active Campaign": "active_campaign",
    "Adobe": "adobe",
    "Adobe Creative Suite": "adobe_creative_suite",
    "Adobe Experience Manager": "adobe_experience_manager",
    "Adobe InDesign": "adobe_indesign",
    "Affirm": "affirm",
    "Afterpay": "afterpay",
    "AIOSEO": "aioseo",
    "Aircall": "aircall",
    "Akamai CDN Solutions": "akamai_cdn_solutions",
    "Akamai Connected Cloud (formerly Linode)": "akamai_connected_cloud_formerly_linode",
    "Amadesa": "amadesa",
    "Amazon AWS Platform": "amazon_aws_platform",
    "Amazon DynamoDB": "amazon_dynamodb",
    "Amazon EC2": "amazon_ec2",
    "Amazon EKS Anywhere": "amazon_eks_anywhere",
    "Amazon Elastic MapReduce (EMR)": "amazon_elastic_mapreduce_emr",
    "Amazon Linux 2": "amazon_linux_2",
    "Amazon Managed Streaming for Apache Kafka (Amazon MSK)": "amazon_managed_streaming_for_apache_kafka_amazon_msk",
    "Amazon Simple Queue Service (SQS)": "amazon_simple_queue_service_sqs",
    "Amazon Simple Storage Service (S3)": "amazon_simple_storage_service_s3",
    "Amazon Virtual Private Cloud (Amazon VPC)": "amazon_virtual_private_cloud_amazon_vpc",
    "Amazon Web Services (AWS)": "amazon_web_services_aws",
    "AMP": "amp",
    "Anaplan": "anaplan",
    "Apache Kafka": "apache_kafka",
    "API Gateway": "api_gateway",
    "Apple Business Manager": "apple_business_manager",
    "Apple Pay": "apple_pay",
    "Apple School Manager": "apple_school_manager",
    "Applicant Pro": "applicant_pro",
    "Argon CI/CD Security": "argon_cicd_security",
    "Aryson MySQL to MSSQL Converter": "aryson_mysql_to_mssql_converter",
    "Authorize.NET": "authorize_net",
    "AutoCAD": "autocad",
    "Autodesk BIM Collaborate": "autodesk_bim_collaborate",
    "Automation Anywhere": "automation anywhere",
    "Autotask": "autotask",
    "Avaya": "avaya",
    "AVEVA PDMS": "aveva_pdms",
    "AWS CloudFormation": "aws_cloudformation",
    "AWS Database Migration Service": "aws_database_migration_service",
    "AWS Glue Data Catalog": "aws_glue_data_catalog",
    "AWS Lambda": "aws_lambda",
    "AWS Transfer for SFTP": "aws_transfer_for_sftp",
    "AWS VPN": "aws_vpn",
    "Azure Active Directory": "azure_active_directory",
    "Azure Active Directory B2C": "azure_active_directory_b2c",
    "Azure App Service": "azure_app_service",
    "Azure Blob Storage": "azure_blob_storage",
    "Azure Data Factory": "azure_data_factory",
    "Azure Data Lake Storage": "azure_data_lake_storage",
    "Azure DNS Zones": "azure_dns_zones",
    "Azure Functions": "azure_functions",
    "Azure Hosting": "azure_hosting",
    "Azure Key Vault": "azure_key_vault",
    "Azure Linux Virtual Machines": "azure_linux_virtual_machines",
    "Azure Logic Apps": "azure_logic_apps",
    "Azure Monitor": "azure_monitor",
    "Azure Web Apps": "azure_web_apps",
    "Backblaze Computer Backup": "backblaze_computer_backup",
    "BambooHR": "bamboohr",
    "Barracuda Networks": "barracuda_networks",
    "Barracuda Spam Firewall": "barracuda_spam_firewall",
    "BigCartel": "bigcartel",
    "BigCommerce": "bigcommerce",
    "BILL": "bill",
    "Blackboard LMS": "blackboard_lms",
    "Bluekai": "bluekai",
    "Boomi": "boomi",
    "BounceExchange": "bounceexchange",
    "Box": "box.com",
    "Braintree": "braintree",
    "Brocade Switches": "brocade_switches",
    "Calendly": "calendly",
    "Calendly for Sales": "calendly_for_sales",
    "Canva": "canva",
    "CAT": "cat",
    "Chargebee": "chargebee",
    "Cisco Secure Firewall Management Center": "cisco_secure_firewall_management_center",
    "Cisco VoIP": "cisco_voip",
    "Cisco VPN": "cisco_vpn",
    "Cisco WebEx Teams": "cisco_webex_teams",
    "Citrix": "citrix",
    "Citrix Hypervisor": "citrix_hypervisor",
    "ClickDimensions": "clickdimensions",
    "Cloudflare Bot Management": "cloudflare_bot_management",
    "Cloudflare Rocket Loader": "cloudflare_rocket_loader",
    "Cloudinary": "cloudinary",
    "Confluence": "confluence",
    "Connect": "connect",
    "Copilot": "copilot",
    "CyberArk": "cyberark",
    "Dash": "dash",
    "Databricks": "databricks",
    "DATAVERSE LTD": "dataverse_ltd",
    "DAX": "dax",
    "dbt": "dbt",
    "Deel": "deel",
    "Dell iDRAC (Integrated Dell Remote Access Controller)": "dell_idrac_integrated_dell_remote_access_controller",
    "Delta Lake": "delta_lake",
    "Demandforce": "demandforce",
    "Demandware": "demandware",
    "Dialpad": "dialpad",
    "Discord": "discord",
    "Domo": "domo",
    "dotMailer": "dotmailer",
    "Drift": "drift",
    "Drip": "drip",
    "Dropbox": "dropbox",
    "Dynamics 365 Business Central": "dynamics 365 business central",
    "Dynamics 365 CRM": "dynamics 365 crm",
    "Dynamics 365 Customer Insights": "dynamics_365_customer_insights",
    "Easy Digital Downloads": "easy_digital_downloads",
    "ECS": "ecs",
    "Ecwid": "ecwid",
    "Eloqua": "eloqua",
    "eMaint CMMS": "emaint_cmms",
    "Emarsys": "emarsys",
    "EMC": "emc",
    "Emma": "emma",
    "Epages": "epages",
    "Epic": "epic",
    "Etix": "etix",
    "Eventbrite": "eventbrite",
    "EventsAir by Centium": "eventsair_by_centium",
    "ExactTarget": "exacttarget",
    "Excel4Apps": "excel4apps",
    "Eyefinity OfficeMate": "eyefinity_officemate",
    "Facebook Meta": "facebook_meta",
    "Facebook Pixel": "facebook_pixel",
    "Facebook SDK": "facebook_sdk",
    "Figma": "figma",
    "Firebase": "firebase",
    "FIS": "fis",
    "FlipHTML5": "fliphtml5",
    "Fortinet": "fortinet",
    "Frame.io": "frame.io",
    "Freshdesk": "freshdesk",
    "GetResponse": "getresponse",
    "GoDaddy Verified": "godaddy_verified",
    "Gong": "gong",
    "Google AlloyDB for PostgreSQL": "google_alloydb_for_postgresql",
    "Google Analytics Ecommerce Tracking": "google_analytics_ecommerce_tracking",
    "Google App Engine": "google_app_engine",
    "Google Cloud": "google_cloud",
    "Google Cloud BigQuery": "google_cloud_bigquery",
    "Google Cloud Platform": "google_cloud_platform",
    "Google Cloud Run": "google_cloud_run",
    "Google Drive": "google_drive",
    "Google Identity Services APIs": "google_identity_services_apis",
    "Google Maps Platform": "google_maps_platform",
    "Google Sheets": "google_sheets",
    "Google Sign-In": "google_signin",
    "Google Translate": "google_translate",
    "Google Workspace": "google workspace",
    "GraphQL": "graphql",
    "Greenhouse.io": "greenhouse_io",
    "Gusto": "gusto",
    "Harmony Email & Collaboration": "harmony_email_collaboration",
    "Helpscout": "helpscout",
    "Hibernate": "hibernate",
    "Highspot": "highspot",
    "Hornetsecurity Spam and Malware Protection": "hornetsecurity_spam_and_malware_protection",
    "Hubspot": "hubspot",
    "HubSpot Content Hub": "hubspot_content_hub",
    "HubSpot Marketing Hub": "hubspot_marketing_hub",
    "IBM Websphere": "ibm_websphere",
    "iCIMS": "icims",
    "iContact": "icontact",
    "iGoDigital": "igodigital",
    "Infoblox DHCP": "infoblox_dhcp",
    "InfusionSoft": "infusionsoft",
    "Insider": "insider",
    "Intel Cloud Services": "intel",
    "Intercom": "intercom",
    "Intershop": "intershop",
    "Intuit": "intuit",
    "Intuit Mailchimp All-in-One Marketing Platform": "intuit_mailchimp_allinone_marketing_platform",
    "iPerceptions": "iperceptions",
    "Jamf": "jamf",
    "Jira": "jira",
    "Jobvite": "jobvite",
    "JTL-Shop 3": "jtl-shop_3",
    "Jumpcloud": "jumpcloud",
    "Juniper Networks SRX-Series Firewalls": "juniper_networks_srxseries_firewalls",
    "Justworks": "justworks",
    "JWT": "jwt",
    "Klarna": "klarna",
    "Klaviyo": "klaviyo",
    "Krux": "krux",
    "Langchain": "langchain",
    "Laserfiche": "laserfiche",
    "LeadForensics": "leadforensics",
    "Leads by Web.com": "leads_by_web_com",
    "Lever": "lever",
    "LinkedIn Recruiter": "linkedin_recruiter",
    "LinkedIn Sales Navigator": "linkedin_sales_navigator",
    "Listrak": "listrak",
    "LiveRamp": "liveramp",
    "Locaweb": "locaweb",
    "Looker": "looker",
    "Looker Studio": "looker_studio",
    "Lotame": "lotame",
    "Mag+": "mag",
    "Magento": "magento",
    "Magento 1.9": "magento_19",
    "Magento 2": "magento_2",
    "Magento 2 Community": "magento_2_community",
    "MailChimp": "mailchimp",
    "Mailchimp Stores": "mailchimp_stores",
    "MailerLite": "mailerlite",
    "Mailshake": "mailshake",
    "MailUp": "mailup",
    "Make": "make.com",
    "Marketo": "marketo",
    "McAfee": "mcafee",
    "MemberClicks": "memberclicks",
    "MessageGears": "messagegears",
    "Meta Ads": "meta_ads",
    "Microsoft 365": "microsoft_365",
    "Microsoft 365 Apps & Services": "microsoft_365_apps_services",
    "Microsoft Active Directory Federation Services": "microsoft_active_directory_federation_services",
    "Microsoft Advanced Group Policy Management": "microsoft_advanced_group_policy_management",
    "Microsoft Defender for Cloud": "microsoft_defender_for_cloud",
    "Microsoft Dynamics": "microsoft_dynamics",
    "Microsoft Entra ID": "microsoft_entra_id",
    "Microsoft Excel": "microsoft_excel",
    "Microsoft Exchange": "microsoft_exchange",
    "Microsoft Exchange Server 2003": "microsoft_exchange_server_2003",
    "Microsoft Fabric": "microsoft_fabric",
    "Microsoft Hyper-V Server": "microsoft_hyperv_server",
    "Microsoft Intune Enterprise Application Management": "microsoft_intune_enterprise_application_management",
    "Microsoft Office": "microsoft_office",
    "Microsoft OneDrive": "microsoft_onedrive",
    "Microsoft OneDrive for Business": "microsoft_onedrive_for_business",
    "Microsoft Power Apps": "microsoft_power_apps",
    "Microsoft Power Automate": "microsoft_power_automate",
    "Microsoft Power Platform": "microsoft_power_platform",
    "Microsoft SharePoint Online": "microsoft_sharepoint_online",
    "Microsoft Sql Server": "microsoft sql server",
    "Microsoft Teams": "microsoft_teams",
    "Microsoft Teams Rooms": "microsoft_teams_rooms",
    "Microsoft Windows Server 2000": "microsoft_windows_server_2000",
    "Microsoft Windows Server 2012": "microsoft_windows_server_2012",
    "Microsoft Word": "microsoft_word",
    "Microsoft.NET Core 3.1": "microsoftnet_core_31",
    "Mindbody": "mindbody",
    "Mist": "mist",
    "Mitel": "mitel",
    "Monday.com": "monday.com",
    "MongoDB": "mongodb",
    "Moodle": "moodle",
    "Myob": "myob",
    "MySQL": "mysql",
    "n8n": "n8n",
    "Navegg": "navegg",
    "Netskope": "netskope",
    "NetSuite": "netsuite",
    "New Relic APM": "new_relic_apm",
    "New Relic Application Monitoring": "new_relic_application_monitoring",
    "Nielsen Display Ads (Formerly eXelate)": "nielsen_display_ads_formerly_exelate",
    "Nosto": "nosto",
    "Octane": "octane",
    "Odoo": "odoo",
    "Odoo CRM": "odoo_crm",
    "Office365": "office365",
    "Okta": "okta",
    "OneTrust Tech Risk & Compliance": "onetrust_tech_risk_compliance",
    "Onit": "onit",
    "Ontraport": "ontraport",
    "OpenCart": "opencart",
    "Oracle Fusion": "oracle fusion",
    "Oracle XML DB": "oracle_xml_db",
    "osCommerce": "oscommerce",
    "Outreach.io": "outreach.io",
    "OVHcloud": "ovhcloud",
    "Pardot": "pardot",
    "Paypal": "paypal",
    "PayPal Payments": "paypal_payments",
    "PEO": "peo",
    "PL/SQL": "plsql",
    "poly": "poly",
    "PostgreSQL": "postgresql",
    "Power Query": "power_query",
    "PrestaShop": "prestashop",
    "Process automation package for service request creation in SAP S/4HANA Utilities": "process_automation_package_for_service_request_creation_in_sap_s4hana_utilities",
    "Procore": "procore",
    "Prometheus": "prometheus",
    "Proofpoint": "proofpoint",
    "Proofpoint Email Security and Protection": "proofpoint_email_security_and_protection",
    "Qlik Sense": "qlik sense",
    "Qualtrics": "qualtrics_intercept",
    "QuickBooks": "quickbooks",
    "Quickbooks Online": "quickbooks_online",
    "Quip": "quip",
    "RabbitMQ": "rabbitmq",
    "Razorpay": "razorpay",
    "Reach Local": "reach_local",
    "Recharge Payments": "recharge_payments",
    "Red Hat OpenShift": "red_hat_openshift",
    "Redis": "redis",
    "Redshift": "redshift",
    "Revel iPad POS": "revel_ipad_pos",
    "Revit": "revit",
    "Rippling": "rippling",
    "S/4HANA": "s/4hana",
    "Sage": "sage",
    "Sakura Internet": "sakura_internet",
    "Salesforce": "salesforce",
    "Salesforce Commerce Cloud": "salesforce_commerce_cloud",
    "Salesforce Marketing Cloud": "salesforce_marketing_cloud",
    "Salesforce Service Cloud": "salesforce_service_cloud",
    "SalesLoft": "salesloft",
    "SALESmanago": "salesmanago",
    "SAP": "sap",
    "SAP B1": "sap b1",
    "SAP S/4HANA": "sap_s4hana",
    "SAP SuccessFactors": "sap_successfactors",
    "Scene7": "scene7",
    "Security": "security",
    "Segment": "segment",
    "Seismic": "seismic",
    "SendInBlue": "sendinblue",
    "ServiceNow": "service_now",
    "ServiceNow Configuration Management Database": "servicenow_configuration_management_database",
    "ServiceTitan": "servicetitan",
    "SES": "ses",
    "Sezzle": "sezzle",
    "SharpSpring": "sharpspring",
    "Shopify": "shopify",
    "Shopify Plus": "shopify_plus",
    "Shopify Product Reviews": "shopify_product_reviews",
    "Siemens PROFIBUS": "siemens_profibus",
    "Siemens SIMATIC S7": "siemens_simatic_s7",
    "Sift Science": "sift_science",
    "Sigma": "sigma",
    "SiteGround": "siteground",
    "Sitelock": "sitelock",
    "Snowflake": "snowflake",
    "Sojern": "sojern",
    "Sophos": "sophos",
    "Spiceworks IP Scanner": "spiceworks_ip_scanner",
    "SQL": "sql",
    "Square, Inc.": "square,_inc_",
    "Squarespace ECommerce": "squarespace_ecommerce",
    "Starfield": "starfield",
    "Stitch": "stitch",
    "Stripe": "stripe",
    "Stripe Payments": "stripe_payments",
    "SysTools VBA Password Recovery": "systools_vba_password_recovery",
    "Tableau": "tableau",
    "TalentEd": "talented",
    "Taleo": "taleo",
    "Taleo Applicant Tracking Systems (ATS)": "taleo_applicant_tracking_systems_ats",
    "Thinkific": "thinkific",
    "Ticket Spice": "ticket_spice",
    "Tor": "tor",
    "TransferWise": "transferwise",
    "TrustArc": "trustarc",
    "Trustwave Seal": "trustwave_seal",
    "TSYS": "tsys",
    "Twilio": "twilio",
    "Uipath": "uipath",
    "UltiPro": "ultipro",
    "Unsplash": "unsplash",
    "Usercentrics": "usercentrics",
    "Vagaro": "vagaro",
    "Viewpoint": "viewpoint",
    "Vincere": "vincere",
    "Virtuemart": "virtuemart",
    "Visio": "visio",
    "VMware": "vmware",
    "Volusion": "volusion",
    "Vonage": "vonage",
    "WhatsApp": "whatsapp",
    "Woo Commerce": "woo_commerce",
    "Woo Commerce Memberships": "woo_ecommerce_memberships",
    "Workable": "workable",
    "Workato": "workato",
    "Workday": "workday",
    "Workday Recruit": "workday_recruit",
    "Xt-commerce": "xt-commerce",
    "Yardi": "yardi",
    "Yoast": "yoast",
    "Yotpo": "yotpo",
    "Zencoder": "zencoder",
    "Zendesk": "zendesk",
    "ZocDoc": "zocdoc",
    "Zoho": "zoho",
    "Zoho CRM": "zoho_crm",
    "Zoho One": "zoho one",
    "ZoomInfo": "zoominfo",
    "Zscaler": "zscaler",
    "Zuora": "zuora",
}

# ─────────────────────────────────────────────────────────────────────────────
# Full Apollo technology taxonomy — loaded from apollo_technologies_full.json
# (the same file the wizard's dropdown is built from). Merged UNDER the curated
# APOLLO_TECHNOLOGY_UIDS above so curated entries win on any key collision,
# preserving existing behaviour exactly while resolving the ~4.6k additional
# technologies a user can now select. Resilient: a missing/corrupt file just
# falls back to the curated dict (this module's contract: NEVER raises).
# ─────────────────────────────────────────────────────────────────────────────
def _load_full_taxonomy() -> dict[str, str]:
    path = Path(__file__).resolve().parents[2] / "apollo_technologies_full.json"
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return {
            str(k): str(v)
            for k, v in data.items()
            if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip()
        }
    except Exception:  # noqa: BLE001 — never let a bad file crash import.
        logger.warning("apollo_technologies_full.json missing/unreadable; "
                       "using curated map only", exc_info=True)
        return {}


# Curated entries override the full taxonomy on collision.
_ALL_TECHNOLOGY_UIDS: dict[str, str] = {
    **_load_full_taxonomy(),
    **APOLLO_TECHNOLOGY_UIDS,
}

# Lower-cased index for the case-insensitive match (built once at import).
_TECH_LOWER: dict[str, str] = {
    k.strip().lower(): v for k, v in _ALL_TECHNOLOGY_UIDS.items()
}

# ─────────────────────────────────────────────────────────────────────────────
# Smart-match indexes (built once at import). Gemini emits technologies FREE-FORM
# ("AWS", "Azure", "PeopleSoft") that don't match Apollo's catalog keys verbatim
# ("Amazon Web Services (AWS)", "Microsoft Azure", "Oracle PeopleSoft"). Rather
# than forward unknown strings to Apollo (which silently ignores them), we try
# progressively looser matches against the REAL catalog and DROP anything that
# still doesn't resolve — so only technologies Apollo actually offers are ever
# sent or shown. See resolve_technology().
# ─────────────────────────────────────────────────────────────────────────────
_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    """Lowercase + strip every non-alphanumeric char ("Microsoft Teams" ->
    "microsoftteams", "Amazon Web Services (AWS)" -> "amazonwebservicesaws")."""
    return _NORM_RE.sub("", (s or "").lower())


# Set of every valid Apollo UID — so a label that IS already a UID
# ("salesforce", "amazon_web_services_aws") is accepted as-is.
_UID_SET: set[str] = set(_ALL_TECHNOLOGY_UIDS.values())

# Punctuation-insensitive index over full display names + their pre-parenthetical
# portion ("Amazon Web Services (AWS)" also indexes "Amazon Web Services").
_TECH_NORM: dict[str, str] = {}
# Parenthetical-nickname index: "Amazon Web Services (AWS)" -> key "aws".
# Strips a leading "formerly " inside the parens (e.g. "(formerly Linode)").
_TECH_PAREN: dict[str, str] = {}
for _disp, _uid in _ALL_TECHNOLOGY_UIDS.items():
    _n = _norm(_disp)
    if _n:
        _TECH_NORM.setdefault(_n, _uid)
    _m = re.search(r"\(([^)]+)\)", _disp)
    if _m:
        _nick = _norm(re.sub(r"^\s*formerly\s+", "", _m.group(1), flags=re.IGNORECASE))
        if _nick:
            _TECH_PAREN.setdefault(_nick, _uid)
        _pre = _norm(_disp[: _m.start()])
        if _pre:
            _TECH_NORM.setdefault(_pre, _uid)

# Curated vendor short-names → canonical catalog display name. Each target is
# resolved to a UID at import and silently skipped if the catalog doesn't carry
# it, so this map can never point Apollo at a dead UID.
_TECH_ALIAS_NAMES: dict[str, str] = {
    "aws": "Amazon Web Services (AWS)",
    "azure": "Microsoft Azure",
    "microsoft azure": "Microsoft Azure",
    "gcp": "Google Cloud",
    "google cloud platform": "Google Cloud",
    "peoplesoft": "Oracle PeopleSoft",
    # 2026-06-11 — observed misses in the live all-products sweep:
    "bigquery": "Google Cloud BigQuery",
    "google bigquery": "Google Cloud BigQuery",
    # Bare "Oracle" overwhelmingly means the database when describing a
    # company's stack (the catalog has no umbrella "Oracle" entry).
    "oracle": "Oracle Database",
}
_TECH_ALIASES: dict[str, str] = {}
for _nick, _disp in _TECH_ALIAS_NAMES.items():
    _u = _ALL_TECHNOLOGY_UIDS.get(_disp) or _TECH_LOWER.get(_disp.lower())
    if _u:
        _TECH_ALIASES[_nick] = _u

# ─────────────────────────────────────────────────────────────────────────────
# Near-miss recovery indexes (2026-06-11). Gemini emits tech names from world
# knowledge, so misses are almost always SPELLING ALIGNMENT, not unknown tech:
#   "Salesforce Data Cloud"  — real product, not an Apollo catalog entry
#   "Google Analytics 4"     — catalog carries "Google Analytics"
#   "Salesforcee"            — plain typo
# These word-level indexes power three conservative fallbacks in _resolve_one
# (parent-product, unique-extension, fuzzy). All three run AFTER every exact
# path, fire at most once per label, and LOG each recovery for audit.
# ─────────────────────────────────────────────────────────────────────────────
def _word_tuple(s: str) -> tuple:
    """("Salesforce Data Cloud") -> ("salesforce", "data", "cloud")."""
    return tuple(w for w in _NORM_RE.sub(" ", (s or "").lower()).split() if w)


_TECH_WORDS: dict[tuple, str] = {}
for _disp, _uid in _ALL_TECHNOLOGY_UIDS.items():
    _t = _word_tuple(_disp)
    if _t:
        _TECH_WORDS.setdefault(_t, _uid)
# Sorted word-tuples — tuples sharing a prefix are contiguous, so the
# unique-extension fallback can scan one bisect block instead of all ~5k.
_TECH_WORDS_SORTED: list[tuple] = sorted(_TECH_WORDS)
# first word -> number of catalog entries starting with it. A SINGLE-word
# parent match ("Salesforce") is only trusted when that word heads a real
# product FAMILY (>= 3 catalog entries) — otherwise "Foo Data Cloud" could
# collapse onto an unrelated one-word catalog entry "Foo".
_TECH_FAMILY_COUNT: dict[str, int] = {}
for _t in _TECH_WORDS:
    _TECH_FAMILY_COUNT[_t[0]] = _TECH_FAMILY_COUNT.get(_t[0], 0) + 1


def _resolve_one(label: str) -> Optional[str]:
    """Resolve a single free-form technology label to a real Apollo UID, or
    None when it isn't in Apollo's catalog (caller drops it). Progressive:
    exact → case-insensitive → already-a-UID → alias → punctuation-insensitive
    → parenthetical nickname."""
    s = (label or "").strip()
    if not s:
        return None
    uid = _ALL_TECHNOLOGY_UIDS.get(s)
    if uid:
        return uid
    low = s.lower()
    uid = _TECH_LOWER.get(low)
    if uid:
        return uid
    uid = _TECH_ALIASES.get(low)
    if uid:
        return uid
    n = _norm(s)
    if n:
        uid = _TECH_NORM.get(n) or _TECH_PAREN.get(n)
        if uid:
            return uid
    # Last resort: the label is ITSELF a valid Apollo UID (e.g. a stored value
    # re-processed). Checked LAST so a casual display name that happens to
    # collide with a narrow product UID — e.g. "azure" is the UID for the
    # "Azure CDN" product — still resolves via the alias path to the intended
    # platform ("Microsoft Azure") first.
    if low in _UID_SET:
        return low

    # ── Near-miss recovery (2026-06-11) — every exact path above missed. ──
    words = _word_tuple(s)

    # 1) PARENT PRODUCT: a catalog name is a word-prefix of the label —
    #    "Salesforce Data Cloud" → "Salesforce". Semantically sound for
    #    filtering (using the sub-product implies using the parent). Longest
    #    parent wins; a 1-word parent must head a product family (>=3 catalog
    #    entries) so the label can't collapse onto an unrelated 1-word entry.
    if len(words) >= 2:
        for k in range(len(words) - 1, 0, -1):
            uid = _TECH_WORDS.get(words[:k])
            if uid and (k >= 2 or _TECH_FAMILY_COUNT.get(words[0], 0) >= 3):
                logger.info(
                    "apollo_technology_map: parent-product fallback %r -> "
                    "%r (uid=%s)", s, " ".join(words[:k]), uid,
                )
                return uid

    # 2) UNIQUE EXTENSION: the label is a word-prefix of exactly ONE catalog
    #    name — "Google Optimize 360" missing? then e.g. "Looker Stu" is not
    #    realistic, but "Power BI Embed" → "Power BI Embedded". Ambiguous
    #    prefixes (>=2 candidates) are NOT guessed.
    if words:
        import bisect as _bisect
        lo = _bisect.bisect_left(_TECH_WORDS_SORTED, words)
        cands = []
        for t in _TECH_WORDS_SORTED[lo:lo + 25]:
            if t[: len(words)] == words:
                if len(t) > len(words):
                    cands.append(t)
            else:
                break
        if len(cands) == 1:
            uid = _TECH_WORDS[cands[0]]
            logger.info(
                "apollo_technology_map: unique-extension fallback %r -> "
                "%r (uid=%s)", s, " ".join(cands[0]), uid,
            )
            return uid

    # 3) FUZZY (typos): >=0.92 similarity on the punctuation-stripped name —
    #    "Salesforcee" → "salesforce". Threshold is high enough that a WRONG
    #    mapping is essentially impossible ("java" vs "javascript" is 0.57).
    n = _norm(s)
    if len(n) >= 5:
        import difflib as _difflib
        close = _difflib.get_close_matches(n, _TECH_NORM.keys(), n=1, cutoff=0.92)
        if close:
            uid = _TECH_NORM[close[0]]
            logger.info(
                "apollo_technology_map: fuzzy fallback %r -> %r (uid=%s)",
                s, close[0], uid,
            )
            return uid
    return None


def resolve_technology(label: Any) -> Optional[str]:
    """Public single-label resolver. Returns the Apollo UID if `label` maps to
    a technology Apollo actually offers, else None. Used by the suggest-targeting
    endpoint to keep ONLY catalog-backed techs in the UI (no dead chips)."""
    if not isinstance(label, str):
        return None
    return _resolve_one(label)


def _flatten(raw: Any) -> List[str]:
    """Normalise the shapes a caller may pass (list / triple dict / str)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, dict):
        return _flatten(raw.get("values"))
    if isinstance(raw, Iterable):
        out: List[str] = []
        for v in raw:
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        return out
    return []


def technologies_to_uids(raw: Any) -> List[str]:
    """Translate free-form technology labels → Apollo technology UIDs.

    A label is KEPT only if it resolves to a technology Apollo actually offers
    (via resolve_technology's progressive matching). Anything that doesn't
    resolve is DROPPED, not forwarded — previously unknown labels were passed
    verbatim and Apollo silently ignored them, so "AWS"/"Azure"/"PeopleSoft"
    (Gemini's casual spellings) quietly did nothing. Now they're recognised as
    real techs; genuinely unknown names are discarded so the filter only ever
    carries valid UIDs.

    Returns a deduplicated, order-preserving list of UIDs.
    """
    labels = _flatten(raw)
    if not labels:
        return []

    out: List[str] = []
    seen: set[str] = set()
    dropped: List[str] = []
    for label in labels:
        uid = _resolve_one(label)
        if uid is None:
            dropped.append(label)
            continue
        if uid in seen:
            continue
        seen.add(uid)
        out.append(uid)

    if dropped:
        logger.info(
            "apollo_technology_map: dropped %d technolog(ies) not in Apollo's "
            "catalog (sent nothing for these): %s",
            len(dropped), dropped,
        )
    return out
