from pydantic import BaseModel, EmailStr, field_serializer
from typing import List, Optional, Dict
from datetime import datetime

class ContactSubmission(BaseModel):
    firstName: str
    lastName: str
    email: str
    phone: Optional[str] = None
    company: str
    message: Optional[str] = "Contact Page Submission"
    source: Optional[str] = "contact_page"

class GenerationRequest(BaseModel):
    campaign_brief: str
    platforms: List[str]

class OTPRequest(BaseModel):
    # M3 fix — EmailStr rejects malformed addresses at the API boundary so
    # scripted callers can't slip 'notanemail' or '<script>' past the schema.
    email: EmailStr
    purpose: str = "login" # login, register, reset_password

class OTPVerify(BaseModel):
    email: EmailStr
    code: str
    purpose: str = "login"

class PasswordChange(BaseModel):
    old_password: Optional[str] = None
    new_password: str
    otp_code: Optional[str] = None

# Unauthenticated password reset (forgot-password flow).
# Must present a valid, unused OTP with purpose='reset_password' for the given
# email — that OTP was emailed to the address so only the mailbox owner can
# use it. See routers/auth.py reset_password.
class PasswordReset(BaseModel):
    email: str
    otp_code: str
    new_password: str

class UserBase(BaseModel):
    email: str
    full_name: Optional[str] = None
    username: Optional[str] = None
    profile_picture_url: Optional[str] = None
    timezone: Optional[str] = "UTC"
    company_name: Optional[str] = None
    company_description: Optional[str] = None
    product_details: Optional[str] = None
    product_url: Optional[str] = None
    onboarded: bool = False
    pricing_plan: Optional[str] = "free"
    # Which product surface the user picked at onboarding — controls sidebar
    # visibility on the frontend. Values: 'pipelyt' | 'gtm' | 'both'.
    # Team members' effective value = their master's product_selection.
    product_selection: Optional[str] = "both"
    subscription_end_date: Optional[datetime] = None
    subscription_cancel_at_period_end: Optional[bool] = False
    business_url: Optional[str] = None
    business_dna: Optional[Dict] = None
    # Team-members feature (Phase 1) — frontend reads these to gate UI:
    #   role='admin' → full access
    #   role='member' → restricted sidebar, locked DNA, no Settings/Billing
    role: Optional[str] = "admin"
    team_owner_id: Optional[int] = None
    # Franchise model: set when this user is a franchisee of a Master.
    # Mutually exclusive with team_owner_id. Franchisees have their own
    # plan + Stripe subscription but post under the Master's brand.
    franchise_of_id: Optional[int] = None
    # Legacy single-brand column (first in the list — kept for back-compat).
    assigned_dna_product_id: Optional[str] = None
    # Authoritative list of admin-assigned brand ids. Frontend composer
    # reads this to decide "locked" (<=1) vs "picker" (2+).
    assigned_dna_product_ids: Optional[List[str]] = None
    disabled: Optional[bool] = False

    @field_serializer('subscription_end_date')
    def serialize_dt(self, dt: datetime, _info):
        return dt.isoformat() + "Z" if dt else None

class UserCreate(BaseModel):
    email: EmailStr
    password: str

class UserProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    username: Optional[str] = None
    profile_picture_url: Optional[str] = None
    timezone: Optional[str] = None
    company_name: Optional[str] = None
    company_description: Optional[str] = None
    product_details: Optional[str] = None
    product_url: Optional[str] = None
    pricing_plan: Optional[str] = None
    business_url: Optional[str] = None
    business_dna: Optional[Dict] = None
    password: Optional[str] = None # For password change

class UserProfileImageUpdate(BaseModel):
    profile_picture_url: str # Base64 or URL

class User(UserBase):
    id: int

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None

class DraftBase(BaseModel):
    content: str
    image_url: Optional[str] = None
    # Static preview image (PNG/JPG) for the Drafts grid. For PDF carousels
    # this is the slide-1 PNG URL so the browser can render it as a plain
    # <img> instead of fighting Chrome's flaky PDF iframe viewer (which
    # renders black at thumbnail sizes / cross-origin). Null for older
    # drafts — frontend falls back to the iframe path then.
    thumbnail_url: Optional[str] = None
    # JSON-encoded list of all slide PNG URLs in order. Used to render
    # slides 2..N in the Drafts modal as native <img> instead of pdf.js.
    slide_thumbnail_urls: Optional[str] = None
    # media_type routes carousel detection on the frontend and selects the
    # right publisher path at /post time. 'document' = LinkedIn PDF carousel,
    # 'image' = single image post, 'video' = video upload, 'text' = no media.
    # Without this field the Drafts page can't tell a carousel from a regular
    # image post and the PDF preview never renders.
    media_type: Optional[str] = None
    targets: Optional[Dict[str, List[str]]] = None # Mapping of platform -> list of account IDs
    dna_product_id: Optional[str] = None            # which brand this draft is for

class DraftCreate(DraftBase):
    pass

class DraftUpdate(DraftBase):
    content: Optional[str] = None
    targets: Optional[Dict[str, List[str]]] = None



class Draft(DraftBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    @field_serializer('created_at', 'updated_at')
    def serialize_dt(self, dt: datetime, _info):
        return dt.isoformat() + "Z"

    class Config:
        from_attributes = True
