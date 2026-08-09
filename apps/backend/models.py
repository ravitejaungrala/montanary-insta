from sqlalchemy import Column, Integer, BigInteger, String, ForeignKey, DateTime, JSON, Boolean
from datetime import datetime
from sqlalchemy.orm import relationship
from sqlalchemy.ext.mutable import MutableDict
from core.database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    
    # Profile & Company Data
    full_name = Column(String, nullable=True)
    username = Column(String, unique=True, index=True, nullable=True)
    profile_picture_url = Column(String, nullable=True)
    timezone = Column(String, default="UTC")
    company_name = Column(String, nullable=True)
    company_description = Column(String, nullable=True)
    product_details = Column(String, nullable=True)
    product_url = Column(String, nullable=True)
    business_url = Column(String, nullable=True)
    business_dna = Column(MutableDict.as_mutable(JSON), nullable=True)
    
    # Onboarding Status
    onboarded = Column(Boolean, default=False)
    pricing_plan = Column(String, default="free") # free, starter, growth, agency, enterprise
    # Product surface the user picked during onboarding — controls which
    # navigation items are visible.
    #   'pipelyt' → AI content generation only (GTM hidden)
    #   'gtm'     → Lead generation only (Content + Create Campaign hidden)
    #   'both'    → Full surface (default for existing users + master-admin
    #               team members whose master picked 'both')
    # Team members inherit their master's selection at read-time (see
    # routers/profile.py). Franchisees pick their own.
    product_selection = Column(String, default="both", nullable=False)
    subscription_end_date = Column(DateTime, nullable=True)
    # True when the user has clicked Cancel in the Stripe Customer Portal.
    # Stripe keeps the subscription active until subscription_end_date —
    # this flag lets the UI switch the label from "Next Billing" to
    # "Cancels on …" without dropping the plan early.
    subscription_cancel_at_period_end = Column(Boolean, default=False, nullable=False)

    # Teams & Roles
    role = Column(String, default="admin") # admin, member
    team_id = Column(BigInteger, ForeignKey("teams.id"), nullable=True)
    # Team-member fields (Phase 1: team members feature)
    # - team_owner_id: pointer to the admin whose plan funds this member.
    #   NULL for standalone users and admins themselves.
    # - assigned_dna_product_id: key into admin.business_dna["products"]
    #   locking the brand this member can post under.
    # - disabled: soft-remove flag — cannot log in but content preserved.
    team_owner_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    # Franchise model: independent operator affiliated to a brand-owning Master.
    # Franchisee has their own pricing_plan + Stripe subscription (unlike team
    # members). Set to Master's id for franchisees; NULL otherwise. Mutually
    # exclusive with team_owner_id (a user is either a team member OR a
    # franchisee OR standalone — never two of these).
    franchise_of_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    # Stripe customer id, set on first successful checkout. Previously we
    # looked customers up by email only; storing the id closes the ambiguity
    # if an email ever collides across accounts.
    stripe_customer_id = Column(String, nullable=True, unique=True)
    # Legacy single-brand column (kept for display back-compat). The list
    # below is authoritative — a member may be assigned multiple brands.
    assigned_dna_product_id = Column(String, nullable=True)
    assigned_dna_product_ids = Column(JSON, nullable=True)
    disabled = Column(Boolean, default=False)
    # Weekly dashboard-stats email digest — set after a successful send so
    # the Monday-morning scheduler (services/weekly_report.py) doesn't
    # double-send if its trigger endpoint fires more than once in the
    # same run window.
    last_weekly_report_sent_at = Column(DateTime, nullable=True)
    # Reputation → Auto-Reply. Page-wide toggle: when TRUE, the
    # auto_reply_worker background job (5-min tick) fetches new comments
    # on this user's recent posts across every connected account and
    # auto-generates + posts an AI reply. Every reply is recorded in
    # AutoReplyLog so we never double-reply to the same comment.
    auto_reply_enabled = Column(Boolean, default=False, nullable=False)
    # Team-member profile (set at invite time by the admin, editable after).
    # All optional. On standalone/admin users these stay NULL — `company_name`
    # above (the admin's own company from DNA) is the only company-level field
    # they need. For team members these describe the EXTERNAL company the
    # member works at + their physical location, so the admin can filter
    # analytics by region/company later.
    member_company_name = Column(String, nullable=True)
    country = Column(String, nullable=True)  # ISO-2 (e.g. "IN", "US")
    country_name = Column(String, nullable=True)  # Display name snapshot
    state = Column(String, nullable=True)    # ISO-2 subdivision code
    state_name = Column(String, nullable=True)    # Display name snapshot
    city = Column(String, nullable=True)          # Free text
    pin_code = Column(String, nullable=True)      # Free text (ZIP / postal)
    
    # Canva Integration
    canva_access_token = Column(String, nullable=True)
    canva_refresh_token = Column(String, nullable=True)
    canva_token_expires_at = Column(DateTime, nullable=True)
    
    # Relationships
    # SocialAccount has two FKs to users.id (`user_id` = owner, `assigned_to_user_id`
    # = delegated member). Explicitly pin this relationship to the owner FK.
    social_accounts = relationship(
        "SocialAccount",
        back_populates="owner",
        foreign_keys="SocialAccount.user_id",
    )
    published_posts = relationship("PublishedPost", back_populates="owner")
    drafts = relationship("Draft", back_populates="owner")
    scheduled_posts = relationship("ScheduledPost", foreign_keys="[ScheduledPost.user_id]", back_populates="owner")
    approved_scheduled_posts = relationship("ScheduledPost", foreign_keys="[ScheduledPost.approved_by]", back_populates="approver")
    team = relationship("Team", foreign_keys=[team_id], back_populates="members")
    owned_team = relationship("Team", foreign_keys="Team.owner_id", back_populates="owner")

class Team(Base):
    __tablename__ = "teams"
    id = Column(BigInteger, primary_key=True, index=True)
    name = Column(String)
    owner_id = Column(BigInteger, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", foreign_keys=[owner_id], back_populates="owned_team")
    members = relationship("User", foreign_keys="User.team_id", back_populates="team")

class SocialAccount(Base):
    __tablename__ = "social_accounts"
    id = Column(BigInteger, primary_key=True, index=True)
    platform = Column(String) # linkedin, facebook, instagram, twitter
    account_id = Column(String) # original account id from platform
    name = Column(String)
    type = Column(String) # profile, page, business
    token = Column(String)
    token_secret = Column(String, nullable=True)
    refresh_token = Column(String, nullable=True)
    profile_picture_url = Column(String, nullable=True)
    follower_count = Column(Integer, default=0)
    # Baseline follower count captured when this SPECIFIC account was FIRST
    # connected. Never overwritten on reconnect of the same account — only
    # set once on insert. If user adds a DIFFERENT account, that new row
    # gets its own initial_follower_count. Used as fallback baseline for KPI
    # % change when no UserAnalyticsSnapshot exists yet (brand-new users).
    initial_follower_count = Column(Integer, default=0)
    initial_connected_at = Column(DateTime, nullable=True)
    last_analytics_sync = Column(DateTime, nullable=True)

    # Team-members feature: when an admin assigns one of their connected
    # social accounts to a team member, this column points to the member.
    # NULL means the account is used by the owner (admin or standalone user).
    # The token still lives on this row; the assignment only routes the
    # account to the member for publishing + connections visibility.
    assigned_to_user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)

    user_id = Column(BigInteger, ForeignKey("users.id"))
    owner = relationship("User", back_populates="social_accounts", foreign_keys=[user_id])

class PublishedPost(Base):
    __tablename__ = "published_posts"
    id = Column(BigInteger, primary_key=True, index=True)
    content = Column(String)
    image_url = Column(String, nullable=True)
    # media_type routes the publisher: 'image' | 'video' | 'document' | 'text'.
    # Kept nullable so old rows (pre-migration) still load as the default
    # behaviour (text-or-image, inferred from image_url).
    media_type = Column(String, nullable=True)
    # Static slide-1 PNG so the Published feed can render carousel previews
    # as a fast native <img> instead of falling back to the "PDF + filename"
    # tile. NULL for legacy rows / non-carousel posts.
    thumbnail_url = Column(String, nullable=True)
    platforms = Column(String) # Comma-separated or JSON
    # dna_product_id — tags which brand this post was generated under so admins
    # can filter analytics by brand. Set at publish time; immutable after.
    dna_product_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user_id = Column(BigInteger, ForeignKey("users.id"))
    owner = relationship("User", back_populates="published_posts")
    platform_posts = relationship("PublishedPostPlatform", back_populates="published_post", cascade="all, delete-orphan")

class PublishedPostPlatform(Base):
    __tablename__ = "published_post_platforms"
    id = Column(Integer, primary_key=True, index=True)
    published_post_id = Column(BigInteger, ForeignKey("published_posts.id"))
    platform = Column(String)
    account_id = Column(String)
    native_post_id = Column(String)
    metrics_json = Column(JSON, nullable=True) # stores likes, comments etc
    last_synced_at = Column(DateTime, nullable=True)
    
    published_post = relationship("PublishedPost", back_populates="platform_posts")

class Draft(Base):
    __tablename__ = "drafts"
    id = Column(BigInteger, primary_key=True, index=True)
    content = Column(String)
    image_url = Column(String, nullable=True)
    media_type = Column(String, nullable=True)  # image | video | document | text
    # Public S3 URL for a static thumbnail that previews this draft in the
    # Drafts grid. For carousels this is the slide_1 PNG (~50-200 KB) so the
    # browser can render it as a native <img> without downloading the
    # whole 9 MB PDF or wrestling with Chrome's flaky PDF iframe viewer.
    # NULL for legacy drafts created before this column was added — the
    # frontend falls back to an iframe of the PDF when null.
    thumbnail_url = Column(String, nullable=True)
    # JSON-encoded list of every slide's PNG URL (in order). Used by the
    # Drafts modal so the user can swipe through slides 1..N as clean
    # <img> tags — no pdf.js worker, no iframe quirks. NULL for legacy
    # rows; frontend gracefully falls back to thumbnail_url + PDF link.
    slide_thumbnail_urls = Column(String, nullable=True)
    targets = Column(String) # JSON string of targets: {platform: [account_id, ...]}
    dna_product_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user_id = Column(BigInteger, ForeignKey("users.id"))
    owner = relationship("User", back_populates="drafts")

class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"
    id = Column(BigInteger, primary_key=True, index=True)
    content = Column(String)
    image_url = Column(String, nullable=True)
    media_type = Column(String, nullable=True)  # image | video | document | text
    # Slide-1 PNG so the Scheduled tab can render carousel previews as a
    # fast native <img> instead of the "PDF DOCUMENT CAROUSEL" tile.
    # Mirrors the Draft/PublishedPost columns. NULL for legacy rows.
    thumbnail_url = Column(String, nullable=True)
    slide_thumbnail_urls = Column(String, nullable=True)
    targets = Column(String) # JSON string of targets: {platform: [account_id, ...]}
    scheduled_for = Column(DateTime)
    timezone = Column(String)
    status = Column(String, default="pending") # pending, published, failed, awaiting_approval
    approved_by = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    post_type = Column(String, default="standard") # standard, agentic
    campaign_brief = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
    dna_product_id = Column(String, nullable=True)
    # YouTube-only metadata. Populated when the scheduled row's targets
    # include `youtube`; ignored on fire for non-YouTube platforms. Stored
    # here because the YouTube videos.insert API needs title/tags/category/
    # privacy AT publish time, and the schedule->fire window can be days
    # so we can't reconstruct them from the user's session.
    youtube_title = Column(String, nullable=True)
    youtube_description = Column(String, nullable=True)
    youtube_tags = Column(JSON, nullable=True)          # list[str]
    youtube_category_id = Column(String, nullable=True) # e.g. "22"
    youtube_privacy = Column(String, nullable=True)     # 'public'|'private'|'unlisted'
    created_at = Column(DateTime, default=datetime.utcnow)

    user_id = Column(BigInteger, ForeignKey("users.id"))
    owner = relationship("User", foreign_keys=[user_id], back_populates="scheduled_posts")
    approver = relationship("User", foreign_keys=[approved_by], back_populates="approved_scheduled_posts")

class PendingOAuthSync(Base):
    __tablename__ = "pending_oauth_syncs"
    id = Column(Integer, primary_key=True, index=True)
    state_or_token = Column(String, index=True) # oauth_token or state
    secret_or_verifier = Column(String) # token_secret or code_verifier
    created_at = Column(DateTime, default=datetime.utcnow)

class UserAnalyticsSnapshot(Base):
    __tablename__ = "user_analytics_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"))
    snapshot_date = Column(DateTime, default=datetime.utcnow)
    total_followers = Column(Integer, default=0)
    total_engagement = Column(Integer, default=0)
    total_likes = Column(Integer, default=0)
    total_comments = Column(Integer, default=0)
    total_shares = Column(Integer, default=0)
    total_reach = Column(Integer, default=0)
    platform_breakdown = Column(JSON, nullable=True) # {platform_name: follower_count}
    
    owner = relationship("User")

class ReconnectRequest(Base):
    """A team member's request for admin to reconnect/refresh a social account.

    Raised when the member detects (or suspects) their assigned connection's
    token is stale. Admin resolves by running the normal reconnect OAuth
    flow on the same SocialAccount row — `/connect-accounts` auto-marks
    pending requests for that account as fulfilled.
    """
    __tablename__ = "reconnect_requests"
    id = Column(Integer, primary_key=True, index=True)
    social_account_id = Column(BigInteger, ForeignKey("social_accounts.id"), nullable=False)
    requester_user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    reason = Column(String, nullable=True)
    status = Column(String, default="pending")  # pending | fulfilled | cancelled
    created_at = Column(DateTime, default=datetime.utcnow)
    fulfilled_at = Column(DateTime, nullable=True)


class TeamInvite(Base):
    """Pending invitation for a team member.

    Admin creates an invite → email with link → invitee clicks, sets
    password + name → User row is created and the invite is marked used.
    Single-use: accepted invites have `used_at` set and can't be replayed.
    """
    __tablename__ = "team_invites"
    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(64), unique=True, index=True, nullable=False)
    email = Column(String, index=True, nullable=False)
    # Admin-provided identifier for this invitee (e.g. "Alice — NY branch lead").
    # Used in the Team list to distinguish pending rows while we wait for them
    # to accept. Suggested as the full_name on acceptance; the invitee can
    # override it when they set their password.
    invited_name = Column(String, nullable=True)
    invited_by = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    # Role granted to the invitee on accept ('admin' | 'user'), validated against
    # the inviter's grantable set. Added via ALTER; defaults to least-privileged.
    role = Column(String, default="user")
    # Invite type: 'team' (member inherits Master's plan) or 'franchise'
    # (invitee brings their own plan/subscription). Drives the accept flow.
    invite_type = Column(String, default="team", nullable=False)
    # Legacy single-brand column (kept for back-compat / primary display). The
    # list below carries one-or-many brand ids and is authoritative on accept.
    assigned_dna_product_id = Column(String, nullable=True)
    assigned_dna_product_ids = Column(JSON, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    used_at = Column(DateTime, nullable=True)
    # Optional: list of SocialAccount.id that should be auto-assigned to the
    # new member on acceptance. Admin picks these in the invite modal.
    assigned_connection_ids = Column(JSON, nullable=True)
    # Optional profile fields (Phase 2 — collected on the invite modal so the
    # admin can filter analytics by region/company without waiting for the
    # member to self-populate). Copied onto the member User row at accept-time.
    member_company_name = Column(String, nullable=True)
    country = Column(String, nullable=True)
    country_name = Column(String, nullable=True)
    state = Column(String, nullable=True)
    state_name = Column(String, nullable=True)
    city = Column(String, nullable=True)
    pin_code = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserOTP(Base):
    __tablename__ = "user_otps"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True)
    otp_code = Column(String)
    purpose = Column(String) # register, login, reset_password
    expires_at = Column(DateTime)
    is_used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class GeneratedCampaign(Base):
    """One row per Agent Post v2 generation run. Powers the gallery picker.

    Stores the 33-variant output so users can return to past generations,
    pick any thumbnail, and use it on a new post. Retention policy is
    90 days for unreferenced rows (lifecycle-managed; see docs §5.9).
    """
    __tablename__ = "generated_campaigns"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), index=True)
    # Short hex id used as the S3 folder prefix (ai_gen/{campaign_id}/...)
    campaign_id = Column(String(32), unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    # Editorial snapshot — powers the gallery list view
    headline = Column(String, nullable=True)
    subheading = Column(String, nullable=True)
    cta = Column(String, nullable=True)
    # Display helpers
    primary_color = Column(String, nullable=True)   # e.g. "#FF6B35"
    aspect_ratio = Column(String, nullable=True)    # e.g. "1:1"
    # Full visuals payload: list of {background_index, scene_type,
    # background_url, background_rating, templates: {T1..T11: url}}
    visuals = Column(JSON, nullable=True)

    owner = relationship("User")


class UserTemplate(Base):
    """User-defined visual template that customizes one of the built-in
    V2 layouts (T1–T5) with their own colors, fonts, positions, and accents.
    At agent-post time, `_generate_visuals_v2` renders all of a user's saved
    templates instead of the default 5 when any exist.

    `overrides` is a free-form JSON blob — schema documented in
    services/image_templates.py::apply_overrides(). Unknown keys are ignored
    so we can add new fields without a migration.
    """
    __tablename__ = "user_templates"
    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), index=True, nullable=False)
    name = Column(String, nullable=False)
    # One of our built-in layouts: "T1" | "T2" | "T3" | "T4" | "T5"
    base_template_id = Column(String, nullable=False)
    overrides = Column(JSON, nullable=True)       # see apply_overrides() schema
    is_default = Column(Boolean, default=False)   # user's go-to for agent posts
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("User")


class GeneratedVideo(Base):
    """Tracks one Phase-1 motion-graphics video generation request.

    Lifecycle:
      1. Row created with status='pending' as soon as the API receives
         the request.
      2. Storyboard agent fills `storyboard_json` and flips status='rendering'.
      3. Remotion Lambda renders, dispatcher writes `video_url` and
         status='ready'.
      4. On any failure, status='failed' + `error_message` populated.

    Kept separate from `generated_campaigns` (image flow) because the
    storyboard schema, asset pipeline, and lifecycle are different
    enough that a shared table would get messy fast. Both share `user_id`
    so the Dashboard's Brand Filter can scope videos by team-member /
    brand once the filter pipes through.
    """
    __tablename__ = "generated_videos"
    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), index=True, nullable=False)
    # Inputs
    campaign_brief = Column(String, nullable=False)
    template = Column(String, default="brand_promo")    # only one for Phase 1
    aspect_ratio = Column(String, default="9:16")
    duration_sec = Column(Integer, default=30)
    dna_product_id = Column(String, nullable=True)      # which brand this is for
    # Pipeline state
    status = Column(String, default="pending")          # pending | rendering | ready | failed
    storyboard_json = Column(JSON, nullable=True)
    video_url = Column(String, nullable=True)           # final S3 MP4 URL
    error_message = Column(String, nullable=True)
    # Metrics for cost / debugging
    render_seconds = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    finished_at = Column(DateTime, nullable=True)

    owner = relationship("User")


class AutoReplyLog(Base):
    """Records every comment the auto-reply worker has replied to.

    Purpose: idempotency. Before the worker generates a reply for a
    freshly-fetched comment, it checks whether (user_id, platform,
    comment_id) already exists here. If it does, we've already replied —
    skip. Without this table, every 5-min tick would re-generate + re-post
    the same reply repeatedly.

    `comment_id` is the platform-native id (LinkedIn commentUrn string,
    Facebook comment id, IG comment id, etc.). Stored as String because
    the shapes vary widely across platforms.

    `status` = "posted" | "failed". Failed rows keep the error_msg for
    diagnosis; they're still inserted so we don't infinite-retry a comment
    that consistently fails (e.g. platform rejected the reply text).
    """
    __tablename__ = "auto_reply_log"
    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), index=True, nullable=False)
    platform = Column(String, index=True, nullable=False)
    native_post_id = Column(String, index=True, nullable=False)
    comment_id = Column(String, index=True, nullable=False)
    reply_text = Column(String, nullable=True)
    status = Column(String, default="posted", nullable=False)  # posted | failed
    error_msg = Column(String, nullable=True)
    replied_at = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)

    owner = relationship("User")
