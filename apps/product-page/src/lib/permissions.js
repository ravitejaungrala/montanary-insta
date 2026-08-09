// Frontend mirror of the backend 3-role RBAC (core/roles.py).
// Master Admin > Admin > User. Keys off `user.role`.

export const ROLE_LABELS = {
  master_admin: 'Master Admin',
  admin: 'Admin',
  user: 'User',
};

// Normalize any legacy value to the new vocab (defensive — the DB rename
// handles existing rows, but a stale token/cache might still carry old values).
function norm(role) {
  if (role === 'master_admin' || role === 'admin' || role === 'user') return role;
  if (role === 'member') return 'admin';
  if (role === 'viewer') return 'user';
  return 'user'; // unknown / empty → least-privileged
}

const WRITE_ROLES = new Set(['master_admin', 'admin']);

export function roleOf(user) {
  return norm(user?.role);
}

/** A read-only User — cannot create/run/connect/add anywhere. */
export function isReadOnly(user) {
  return roleOf(user) === 'user';
}

/** Master Admin or Admin — may perform work actions. */
export function canWrite(user) {
  return WRITE_ROLES.has(roleOf(user));
}

/** Only the Master Admin (owner) manages billing / plan / buying credits. */
export function canManageBilling(user) {
  return roleOf(user) === 'master_admin';
}

export function isMasterAdmin(user) {
  return roleOf(user) === 'master_admin';
}

/** Roles this user may grant when adding/inviting a member. */
export function grantableRoles(user) {
  const r = roleOf(user);
  if (r === 'master_admin') return ['admin', 'user'];
  if (r === 'admin') return ['user'];
  return [];
}

/** Can this user add members at all? */
export function canAddMembers(user) {
  return grantableRoles(user).length > 0;
}

export function roleLabel(role) {
  return ROLE_LABELS[norm(role)] || 'User';
}

// ────────────────────────────────────────────────────────────────────────
// Product surface selection — matches user.product_selection on the backend.
// Values: 'pipelyt' | 'gtm' | 'both'. Defaults to 'both' if missing so
// legacy users (created before the field existed) keep their old behavior.
// Team members see their master's effective value (backend fills it in on
// GET /profile).
// ────────────────────────────────────────────────────────────────────────
function productSelectionOf(user) {
  const raw = user?.product_selection;
  if (raw === 'pipelyt' || raw === 'gtm' || raw === 'both') return raw;
  return 'both';
}

/** Can the user see Pipelyt UI (content generation, calendar, etc.)? */
export function canAccessPipelyt(user) {
  const sel = productSelectionOf(user);
  return sel === 'pipelyt' || sel === 'both';
}

/** Can the user see the GTM (Nexus) surface? */
export function canAccessGtm(user) {
  const sel = productSelectionOf(user);
  return sel === 'gtm' || sel === 'both';
}

/** Only master_admins pick their own product surface (team members inherit). */
export function canChangeProductSelection(user) {
  return !user?.team_owner_id;
}
