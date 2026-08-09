// Allowed values for the NewCampaign targeting filters. MUST stay in sync
// with apps/backend/nexus/routers/analyze_targeting.py — Gemini's auto-fill
// is filtered against the backend's lists, so any mismatch silently drops
// values from the frontend.

// Full Apollo technology taxonomy (display name → UID). Copied from
// apps/backend/apollo_technologies_full.json — used to build
// ALL_TECHNOLOGIES below.
import APOLLO_TECHNOLOGIES_MAP from './apolloTechnologies.json';

export const COUNTRIES = [
  'United States', 'United Kingdom', 'Canada', 'Australia', 'New Zealand',
  'Germany', 'France', 'Netherlands', 'Sweden', 'Denmark', 'Norway', 'Finland',
  'Ireland', 'Switzerland', 'Belgium', 'Austria', 'Spain', 'Italy', 'Portugal',
  'Poland', 'Czech Republic', 'Luxembourg',
  'Singapore', 'India', 'Japan', 'South Korea', 'Taiwan', 'Hong Kong',
  'Malaysia', 'Indonesia', 'Philippines', 'Thailand', 'Vietnam', 'China',
  'United Arab Emirates', 'Saudi Arabia', 'Israel', 'Qatar', 'Bahrain',
  'South Africa', 'Nigeria', 'Kenya', 'Egypt',
  'Brazil', 'Mexico', 'Argentina', 'Colombia', 'Chile',
];

export const INDUSTRIES = [
  // 2026-06-11: Apollo's FULL native industry taxonomy (145 labels), 1:1 with
  // what Apollo's own industry filter accepts. Kept in sync with the backend's
  // ALLOWED_INDUSTRIES (analyze_targeting.py) and APOLLO_NATIVE_INDUSTRY_TAGS.
  'Accounting', 'Agriculture', 'Airlines/Aviation', 'Alternative Dispute Resolution',
  'Alternative Medicine', 'Animation', 'Apparel & Fashion', 'Architecture & Planning',
  'Arts & Crafts', 'Automotive', 'Aviation & Aerospace', 'Banking', 'Biotechnology',
  'Broadcast Media', 'Building Materials', 'Business Supplies & Equipment',
  'Capital Markets', 'Chemicals', 'Civic & Social Organization', 'Civil Engineering',
  'Commercial Real Estate', 'Computer & Network Security', 'Computer Games',
  'Computer Hardware', 'Computer Networking', 'Computer Software', 'Construction',
  'Consumer Electronics', 'Consumer Goods', 'Consumer Services', 'Cosmetics', 'Dairy',
  'Defense & Space', 'Design', 'E-Learning', 'Education Management',
  'Electrical/Electronic Manufacturing', 'Entertainment', 'Environmental Services',
  'Events Services', 'Executive Office', 'Facilities Services', 'Farming',
  'Financial Services', 'Fine Art', 'Fishery', 'Food & Beverages', 'Food Production',
  'Fund-Raising', 'Furniture', 'Gambling & Casinos', 'Glass, Ceramics & Concrete',
  'Government Administration', 'Government Relations', 'Graphic Design',
  'Health, Wellness & Fitness', 'Higher Education', 'Hospital & Health Care',
  'Hospitality', 'Human Resources', 'Import & Export', 'Individual & Family Services',
  'Industrial Automation', 'Information Services', 'Information Technology & Services',
  'Insurance', 'International Trade & Development', 'Internet', 'Investment Banking',
  'Investment Management', 'Judiciary', 'Law Enforcement', 'Law Practice',
  'Legal Services', 'Leisure, Travel & Tourism', 'Libraries',
  'Logistics & Supply Chain', 'Luxury Goods & Jewelry', 'Machinery',
  'Management Consulting', 'Maritime', 'Market Research', 'Marketing & Advertising',
  'Mechanical Or Industrial Engineering', 'Media Production', 'Medical Devices',
  'Medical Practice', 'Mental Health Care', 'Military', 'Mining & Metals',
  'Motion Pictures & Film', 'Music', 'Nanotechnology', 'Newspapers',
  'Nonprofit Organization Management', 'Oil & Energy', 'Online Media',
  'Outsourcing/Offshoring', 'Package/Freight Delivery', 'Packaging & Containers',
  'Paper & Forest Products', 'Performing Arts', 'Pharmaceuticals', 'Philanthropy',
  'Photography', 'Plastics', 'Political Organization', 'Primary/Secondary Education',
  'Printing', 'Professional Training & Coaching', 'Program Development',
  'Public Policy', 'Public Relations & Communications', 'Public Safety', 'Publishing',
  'Railroad Manufacture', 'Ranching', 'Real Estate',
  'Recreational Facilities & Services', 'Religious Institutions',
  'Renewables & Environment', 'Research', 'Restaurants', 'Retail',
  'Security & Investigations', 'Semiconductors', 'Shipbuilding', 'Sporting Goods',
  'Sports', 'Staffing & Recruiting', 'Supermarkets', 'Telecommunications', 'Textiles',
  'Think Tanks', 'Tobacco', 'Translation & Localization',
  'Transportation/Trucking/Railroad', 'Utilities', 'Venture Capital & Private Equity',
  'Veterinary', 'Warehousing', 'Wholesale', 'Wine & Spirits', 'Wireless',
  'Writing & Editing',
];

export const ROLES = [
  'CEO', 'CFO', 'CTO', 'COO', 'CMO', 'CPO', 'CISO', 'CRO', 'CDO',
  'President', 'Founder', 'Co-Founder', 'Managing Director', 'General Manager',
  'VP Engineering', 'VP Sales', 'VP Marketing', 'VP Product', 'VP Operations',
  'VP Finance', 'VP IT', 'VP Business Development', 'VP Customer Success',
  'Director of Engineering', 'Director of Sales', 'Director of Marketing',
  'Director of Product', 'Director of Operations', 'Director of IT',
  'Head of Engineering', 'Head of Product', 'Head of Sales', 'Head of Marketing',
  'Head of Data', 'Head of Operations', 'Engineering Manager', 'Product Manager',
  'Project Manager', 'Sales Manager', 'Marketing Manager',
  'Senior Software Engineer', 'Software Engineer', 'Staff Engineer',
  'Principal Engineer', 'Data Scientist', 'Data Engineer', 'ML Engineer',
  'DevOps Engineer', 'Platform Engineer', 'Site Reliability Engineer',
  'Account Executive', 'Business Development Manager',
  'Sales Development Representative', 'Growth Manager', 'Customer Success Manager',
  'Account Manager', 'IT Manager',
];

// 2026-06-02 — "Any" and "Pre-revenue" removed. Both mapped to "no
// filter" in the backend (discovery_apollo._revenue_band_to_apollo
// returns None for them), so they served no purpose — they just
// looked like a selected value while doing nothing. The dropdown now
// shows only real revenue bands; if the user picks nothing, no
// revenue_range filter is sent to Apollo.
export const REVENUE_OPTIONS = [
  '< $1M', '$1M – $10M', '$10M – $50M',
  '$50M – $200M', '$200M – $1B', '$1B+',
];

// Dollar bounds per band — MUST stay in sync with _REVENUE_BAND_BOUNDS in
// apps/backend/nexus/services/discovery_apollo.py. The wizard offers free
// multi-select over these bands and collapses the picks into a single
// {min, max} window spanning the lowest selected min → highest selected max
// (Apollo only supports one contiguous range — it has no array form). Picks
// don't have to be adjacent; if they aren't, the span includes the gap (the
// UI flags this). The 1B+ upper bound is a sentinel "open top".
export const REVENUE_BAND_BOUNDS = {
  '< $1M':        { min: 0,             max: 1_000_000 },
  '$1M – $10M':   { min: 1_000_000,     max: 10_000_000 },
  '$10M – $50M':  { min: 10_000_000,    max: 50_000_000 },
  '$50M – $200M': { min: 50_000_000,    max: 200_000_000 },
  '$200M – $1B':  { min: 200_000_000,   max: 1_000_000_000 },
  '$1B+':         { min: 1_000_000_000, max: 1_000_000_000_000 },
};

// Apollo's `person_seniorities` closed enum — MUST stay in sync with
// ALLOWED_SENIORITIES in apps/backend/nexus/routers/analyze_targeting.py.
// Sent verbatim to Apollo's lead-search API.
export const SENIORITIES = [
  'c_suite', 'vp', 'head', 'director', 'manager', 'senior', 'entry',
];

// DEPARTMENTS (person_departments) removed 2026-06-08 — titles already
// imply department; the chip row + Apollo filter were dropped.

// Free-form examples shown to the user as autocomplete suggestions for
// the TARGET TECHNOLOGIES chip row. Apollo accepts ANY string here — the
// chip input is not closed against this list. New entries the user types
// are forwarded to Apollo's `currently_using_any_of_technology_uids`.
// Shown FIRST when the field is focused with no query, so the most common
// picks stay one keystroke away even though the full Apollo taxonomy
// (ALL_TECHNOLOGIES below) is searchable.
export const COMMON_TECHNOLOGIES = [
  'Salesforce', 'HubSpot', 'Mulesoft', 'SAP', 'Oracle',
  'AWS', 'Google Cloud', 'Microsoft Azure',
  'Snowflake', 'Databricks', 'BigQuery', 'Redshift',
  'Workday', 'NetSuite', 'ServiceNow',
  'Slack', 'Microsoft Teams', 'Zoom',
  'Jira', 'GitHub', 'GitLab', 'Bitbucket',
  'Stripe', 'Shopify', 'Segment', 'MongoDB', 'PostgreSQL',
];

// COMMON first (popular picks on empty focus), then the rest of the
// taxonomy, deduped. ~5k entries — FreeTextChips slices to 30 per query so
// filtering stays cheap.
export const ALL_TECHNOLOGIES = (() => {
  const seen = new Set(COMMON_TECHNOLOGIES);
  const rest = Object.keys(APOLLO_TECHNOLOGIES_MAP).filter((name) => {
    if (seen.has(name)) return false;
    seen.add(name);
    return true;
  });
  return [...COMMON_TECHNOLOGIES, ...rest];
})();
