# Plan Configurations and Quotas
# April 2026

PLAN_CONFIG = {
    "free": {
        "quotas": {
            "posts": 5,
            "images": 15,
            "brands": 1,
            "channels": 1,
            "team_seats": 0,
            "franchise_seats": 0,
        },
        "features": {
            "full_scheduling": False,
            "canva": False,
            "analytics": False,
            "teams": False,
            "franchise": False,
            "video": False,
        }
    },
    "starter": {
        "quotas": {
            "posts": 30,
            "images": 60,
            "brands": 2,
            "channels": 3,
            "team_seats": 0,
            "franchise_seats": 0,
        },
        "features": {
            "full_scheduling": False,
            "canva": False,
            "analytics": False,
            "teams": False,
            "franchise": False,
            "video": False,
        }
    },
    "growth": {
        "quotas": {
            "posts": 60,
            "images": 120,
            "brands": 5,
            "channels": 5,
            "team_seats": 0,
            "franchise_seats": 0,
        },
        "features": {
            "full_scheduling": True,
            "canva": True,
            "analytics": True,
            "teams": False,
            "franchise": False,
            "video": False,
        }
    },
    "agency": {
        "quotas": {
            # Agency hard cap: 120 AI posts + 240 AI images / month. Pricing
            # page, Onboarding plan cards, and the Settings -> Billing quota
            # card must all show the same numbers to match this enforcement.
            # Channels remain unlimited (999999).
            "posts": 120,
            "images": 240,
            "brands": 10,
            "channels": 999999,
            "team_seats": 15,
            "franchise_seats": 5,
        },
        "features": {
            "full_scheduling": True,
            "canva": True,
            "analytics": True,
            "teams": True,
            "franchise": True,
            "video": True,
        }
    },
    "enterprise": {
        "quotas": {
            "posts": 999999,
            "images": 999999,
            "brands": 999999,
            "channels": 999999,
            "team_seats": 999999,
            "franchise_seats": 999999,
        },
        "features": {
            "full_scheduling": True,
            "canva": True,
            "analytics": True,
            "teams": True,
            "franchise": True,
            "video": True,
        }
    }
}

# Aliases for backward compatibility
PLAN_CONFIG["basic"] = PLAN_CONFIG["starter"]
PLAN_CONFIG["pro"] = PLAN_CONFIG["growth"]
