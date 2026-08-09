import React, { useState } from 'react';
import {
  LayoutDashboard,
  Users,
  CheckCircle2,
  LineChart,
  Rocket,
  PlusSquare,
  Cpu,
  Calendar,
  ListTodo,
  FileEdit,
  Plug,
  LogOut,
  Settings,
  X,
  ChevronRight,
  HelpCircle,
  PlusCircle,
  Lock,
  BookOpen,
  MessageSquare,
  Activity,
} from 'lucide-react';
import { LANDING_HOME_HREF, NEXUS_ENABLED } from '../utils/config';
import { isReadOnly, canAccessPipelyt, canAccessGtm } from '../lib/permissions';

const Sidebar = ({ activeTab, setActiveTab, handleLogout, isOpen, onClose, user, isExpired = false }) => {
  const [isHoverExpanded, setIsHoverExpanded] = useState(false);
  // On mobile (when isOpen is true) always show expanded so labels are readable on touch.
  const isExpanded = isOpen || isHoverExpanded;

  // Team-members feature (Phase 1): members get a stripped sidebar.
  // They cannot manage team, billing, brand DNA, or account settings beyond
  // password + timezone. "Posts" (manual post) is added specifically for them
  // per product decision.
  const isMember = isReadOnly(user);  // read-only User gets the restricted nav
  // Admins on Agency/Enterprise see the Team entry; non-teams plans hide it.
  // Franchisees show inline on the Team roster (with a badge), so there's
  // no separate Franchisees nav.
  const canManageTeam = !isMember && (user?.pricing_plan === 'agency' || user?.pricing_plan === 'enterprise');

  // Product surface gating (Team + Settings + GTM are the only cross-surface
  // items; everything else — Dashboard, Create, Campaign Performance,
  // Calendar, Scheduled, Published, Drafts, Connections, Analytics,
  // Reputation, User Guide — belongs to Pipelyt and is hidden when the user
  // picked 'gtm' only). GTM is hidden when the user picked 'pipelyt' only.
  const showPipelyt = canAccessPipelyt(user);
  const showGtm     = canAccessGtm(user);

  // Pipelyt-only groups. Empty arrays → the group filter below drops them
  // entirely so we don't render an empty section header.
  const pipelytNavItems = isMember
    ? [
        { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
        { id: 'campaign-performance', label: 'Campaign Performance', icon: Activity },
      ]
    : [
        { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
        { id: 'create', label: 'Create Campaign', icon: PlusCircle },
        { id: 'campaign-performance', label: 'Campaign Performance', icon: Activity },
      ];

  const pipelytContentItems = [
    { id: 'calendar', label: 'Calendar', icon: Calendar },
    { id: 'scheduled', label: 'Scheduled', icon: ListTodo },
    { id: 'published', label: 'Published', icon: CheckCircle2 },
    { id: 'drafts', label: 'Drafts', icon: FileEdit },
  ];

  // Workspace = Pipelyt-specific items (connections/analytics/reputation/
  // guide) PLUS cross-surface items (settings, team). We split them so the
  // Pipelyt-specific ones drop when the user picked 'gtm' only, but Team +
  // Settings stay visible regardless.
  const pipelytWorkspaceItems = [
    { id: 'connections', label: 'Connections', icon: Plug },
    { id: 'analytics', label: 'Analytics', icon: LineChart },
    { id: 'reputation', label: 'Reputation', icon: MessageSquare },
    { id: 'guide', label: 'User Guide', icon: BookOpen },
  ];

  const alwaysVisibleWorkspaceItems = isMember
    ? [
        { id: 'settings', label: 'Account', icon: Settings },
      ]
    : [
        ...(canManageTeam ? [{ id: 'team', label: 'Team', icon: Users }] : []),
        { id: 'settings', label: 'Settings', icon: Settings },
      ];

  // Compose menuGroups. Each group's items are filtered by product surface.
  // Groups with 0 items after filtering are dropped so we don't render empty
  // section headers.
  const rawGroups = [
    { title: 'Navigation', items: showPipelyt ? pipelytNavItems : [] },
    { title: 'Content',    items: showPipelyt ? pipelytContentItems : [] },
    {
      title: 'Workspace',
      items: [
        ...(showPipelyt ? pipelytWorkspaceItems : []),
        ...alwaysVisibleWorkspaceItems,
      ],
    },
    // GTM — hidden entirely when user picked 'pipelyt' only, or when the
    // env feature flag is off. Team members with a master who has GTM
    // enabled can VIEW the surface (writes gated separately).
    {
      title: '',
      items: (NEXUS_ENABLED && showGtm && (!isMember || showGtm))
        ? [{ id: 'nexus', label: 'GTM', icon: Rocket }]
        : [],
    },
  ];
  const menuGroups = rawGroups.filter((g) => (g.items || []).length > 0);

  return (
    <>
      {/* Mobile Backdrop */}
      <div
        className={`fixed inset-0 z-40 lg:hidden bg-[#2B2926]/40 backdrop-blur-sm transition-all duration-300 ${isOpen ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'}`}
        onClick={onClose}
      />

      <aside
        onMouseEnter={() => setIsHoverExpanded(true)}
        onMouseLeave={() => setIsHoverExpanded(false)}
        className={`fixed lg:static inset-y-0 left-0 z-50 bg-white border-r border-[#2B2926]/10 flex flex-col h-full shrink-0 transition-all duration-300 ease-in-out ${isOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'} ${isExpanded ? 'w-[260px]' : 'w-[84px]'} shadow-2xl lg:shadow-none overflow-hidden`}
      >
        {/* Logo Section */}
        <div className={`px-4 flex items-center ${isExpanded ? 'justify-between' : 'justify-center'} h-16 border-b border-[#2B2926]/10`}>
          <a href={LANDING_HOME_HREF} className="flex items-center gap-3 group">
            <img
              src="/montanary-elevators.png"
              alt="Montanary Elevators Logo"
              className={`${isExpanded ? 'w-9 h-9' : 'w-11 h-11'} object-contain transition-all duration-300 group-hover:scale-105`}
            />
            {isExpanded && (
              <div className="flex flex-col animate-in fade-in slide-in-from-left-2 duration-300">
                <h1 className="text-[15px] font-bold text-[#2B2926] tracking-tight leading-none">Montanary Elevators</h1>
                <p className="text-[9px] font-semibold tracking-[0.12em] mt-1 uppercase leading-tight">
                  <span className="text-[#F55600]">All Channels</span>
                  <span className="text-[#2B2926]/30"> · </span>
                  <span className="text-[#10B981]">One Intelligence</span>
                </p>
              </div>
            )}
          </a>

          {isExpanded && (
            <button onClick={onClose} className="lg:hidden p-2 text-[#2B2926]/40 hover:text-[#F55600] transition-colors rounded-lg hover:bg-[#F55600]/5">
              <X className="w-5 h-5" />
            </button>
          )}
        </div>

        {/* Navigation Sections */}
        <nav className="flex-1 overflow-y-auto px-3 py-1.5 space-y-0.5 custom-scrollbar overflow-x-hidden">
          {menuGroups.map((group, gIdx) => (
            <div key={group.title} className="space-y-0.5">
              {isExpanded && gIdx > 0 && group.title && (
                <h3 className="px-3 mt-2 mb-0.5 text-[10px] font-semibold text-[#67655E] tracking-[0.14em] uppercase animate-in fade-in duration-500 border-l-2 border-[#F55600] pl-2.5">
                  {group.title}
                </h3>
              )}
              {group.items.map((item) => {
                const isActive = activeTab === item.id;
                const isLocked = isExpired && item.id !== 'settings';
                return (
                  <button
                    key={item.id}
                    onClick={() => {
                      if (!isLocked) {
                        setActiveTab(item.id);
                        if (onClose) onClose(); // Auto-close on mobile
                      }
                    }}
                    disabled={isLocked}
                    className={`w-full relative flex items-center transition-all duration-200 rounded-xl group/btn overflow-hidden ${isExpanded ? 'px-2.5 py-0.5' : 'justify-center py-0.5 px-0'} ${isActive ? 'bg-[#F55600]/10' : 'hover:bg-[#2B2926]/[0.04]'} ${isLocked ? 'opacity-40 cursor-not-allowed filter grayscale' : ''}`}
                  >
                    {/* Active state: light-orange tint row + solid orange icon
                        tile + a sharp orange edge bar — clean and professional
                        (a full solid-colour row reads as heavy in a tall menu). */}
                    {isActive && (
                      <div className="absolute left-0 top-1.5 bottom-1.5 w-1 bg-[#F55600] rounded-r-full" />
                    )}
                    <div className={`flex items-center gap-3 ${!isExpanded && 'justify-center w-full'}`}>
                      <div className={`transition-all duration-200 flex items-center justify-center rounded-lg w-7 h-7 ${isActive ? 'bg-[#F55600] text-white shadow-sm shadow-[#F55600]/30' : 'text-[#67655E] group-hover/btn:text-[#2B2926] group-hover/btn:bg-[#2B2926]/[0.05]'}`}>
                        {isLocked && isExpanded ? <Lock className="w-3.5 h-3.5" /> : <item.icon className={`w-[18px] h-[18px] ${isLocked ? 'opacity-50' : ''}`} />}
                      </div>

                      {isExpanded && (
                        <div className="flex-1 text-left min-w-0 animate-in fade-in slide-in-from-left-2 duration-300">
                          <p className={`text-[13.5px] leading-tight font-semibold ${isActive ? 'text-[#2B2926]' : 'text-[#2B2926] group-hover/btn:text-[#2B2926]'}`}>
                            {item.label}
                          </p>
                        </div>
                      )}

                      {!isExpanded && isLocked && (
                         <div className="absolute top-1 right-1">
                            <Lock className="w-2.5 h-2.5 text-[#2B2926]/30" />
                         </div>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          ))}
        </nav>

        {/* Footer / User Profile */}
        <div className="p-2 border-t border-[#2B2926]/10">
          <div
            onClick={() => {
              setActiveTab('settings');
              if (onClose) onClose(); // Auto-close on mobile
            }}
            className={`transition-all duration-200 rounded-xl border flex items-center group/profile cursor-pointer overflow-hidden ${isExpanded ? 'p-1.5 bg-white border-[#2B2926]/10 hover:border-[#F55600]/50 hover:shadow-md' : 'p-0 border-transparent justify-center'}`}
          >
            <div className={`flex items-center ${isExpanded ? 'gap-2.5' : 'gap-0'} min-w-0`}>
              <div className="w-8 h-8 rounded-lg bg-[#F55600] flex items-center justify-center text-white font-semibold text-[14px] shrink-0 overflow-hidden shadow-sm">
                {user?.profile_picture_url ? (
                  <img src={user.profile_picture_url} alt="Profile" className="w-full h-full object-cover" />
                ) : (
                  user?.full_name ? user.full_name[0].toUpperCase() : (user?.email ? user.email[0].toUpperCase() : 'U')
                )}
              </div>

              {isExpanded && (
                <div className="flex-1 min-w-0 animate-in fade-in slide-in-from-left-2 duration-300">
                  <p className="text-[13.5px] font-semibold text-[#2B2926] truncate leading-tight">{user?.full_name || user?.username || 'User'}</p>
                  <p className="text-[10px] text-[#F55600] font-semibold truncate uppercase tracking-[0.14em] mt-0.5">{user?.company_name || 'My Business'}</p>
                </div>
              )}
            </div>

            {isExpanded ? (
               <button
                onClick={(e) => { e.stopPropagation(); handleLogout(); }}
                className="ml-auto p-2 text-[#2B2926]/40 hover:text-[#F55600] rounded-xl transition-all duration-200 hover:bg-[#F55600]/5"
                title="Sign Out"
              >
                <LogOut className="w-4 h-4" />
              </button>
            ) : null}
          </div>

          {!isExpanded && (
            <button
              onClick={handleLogout}
              className="mt-2 w-10 h-10 rounded-xl bg-[#2B2926]/[0.03] flex items-center justify-center text-[#2B2926]/40 hover:text-[#F55600] hover:bg-[#F55600]/5 border border-transparent hover:border-[#F55600]/30 transition-all mx-auto"
            >
              <LogOut className="w-4 h-4" />
            </button>
          )}

          {isExpanded && (
            <p className="mt-1.5 text-center text-[9px] font-bold tracking-[0.1em] text-[#2B2926]/40 whitespace-nowrap animate-in fade-in duration-300">
              Powered by <span className="text-[#F55600] font-black">NeuzenAI</span>
            </p>
          )}
        </div>
      </aside>
    </>
  );
};

export default Sidebar;
