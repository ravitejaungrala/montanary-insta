import React, { useState } from 'react';
import { BookOpen, Activity, DollarSign, Rocket, BarChart3, Bot, Shield, ChevronDown, Zap, Layout, CheckCircle2, AlertCircle, Lightbulb, Target, ArrowRight, FileSpreadsheet, TrendingUp, AlertTriangle, Sparkles, Info, Users, Plug, Calendar, PlusCircle, Image as ImageIcon, Video, Type, FileText, MessageSquare, Smile, RotateCcw, Settings, Lock, Filter, PieChart, Clock, Send, Tag } from 'lucide-react';
import { clsx } from 'clsx';
import { motion, AnimatePresence } from 'framer-motion';

/* --- Documentation UI Components --- */

const AccordionItem = ({ isOpen, onClick, children, className }) => {
    return (
        <div className={clsx("border-2 border-[#2B2926]/30 rounded-[32px] overflow-hidden transition-all duration-300 shadow-sm hover:shadow-md bg-white", className)}>
            {children(isOpen, onClick)}
        </div>
    );
};

const AccordionTrigger = ({ children, isOpen, onClick, className }) => (
    <button
        onClick={onClick}
        className={clsx(
            "w-full flex items-center justify-between p-6 text-left transition-colors",
            isOpen ? "bg-white border-b-2 border-orange-100/50" : "bg-white hover:bg-slate-50",
            className
        )}
    >
        {children}
        <div className={clsx("p-2 rounded-xl transition-all duration-300", isOpen ? "bg-[#F55600] text-white shadow-lg shadow-orange-200" : "bg-slate-100 text-[#2B2926]")}>
            <ChevronDown
                className={clsx(
                    "w-5 h-5 transition-transform duration-500",
                    isOpen && "transform rotate-180"
                )}
            />
        </div>
    </button>
);

const AccordionContent = ({ children, isOpen, className }) => (
    <AnimatePresence>
        {isOpen && (
            <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.4, ease: "circOut" }}
                className="overflow-hidden"
            >
                <div className={clsx("p-8 bg-white", className)}>
                    {children}
                </div>
            </motion.div>
        )}
    </AnimatePresence>
);

// --- Typography & Layout ---

const DocSection = ({ title, description, children }) => (
    <div className="mb-12 last:mb-0">
        <div className="mb-6 border-b-2 border-[#2B2926]/30 pb-4">
            <h3 className="text-xl font-semibold text-[#0f172A] flex items-center gap-3 uppercase tracking-tight">
                <div className="w-1.5 h-6 bg-[#F55600] rounded-full"></div>
                {title}
            </h3>
            {description && <p className="text-sm font-medium text-[#2B2926] mt-2 pl-4 leading-relaxed max-w-3xl">{description}</p>}
        </div>
        <div className="pl-0 md:pl-4 space-y-6">
            {children}
        </div>
    </div>
);

const DocStep = ({ number, title, children, isLast }) => (
    <div className="relative pl-12 md:pl-14 pb-8 last:pb-0">
        {/* Timeline Line */}
        {!isLast && <div className="absolute left-[17px] md:left-[19px] top-10 bottom-0 w-0.5 bg-slate-100"></div>}

        {/* Number Badge */}
        <div className="absolute left-0 top-0 w-9 h-9 md:w-10 md:h-10 rounded-xl bg-white border-2 border-orange-100 text-[#F55600] font-semibold flex items-center justify-center text-sm md:text-base shadow-sm z-10 transition-all hover:border-[#F55600] hover:scale-110 duration-300">
            {number}
        </div>

        <div className="pt-2">
            <h4 className="font-semibold text-[#0f172A] text-lg mb-3 tracking-tight">{title}</h4>
            <div className="text-sm font-medium text-[#2B2926] leading-relaxed space-y-4">
                {children}
            </div>
        </div>
    </div>
);

const DocCallout = ({ type = 'info', title, children }) => {
    const styles = {
        info: { bg: 'bg-white', border: 'border-orange-100', text: 'text-orange-950', icon: Info, iconColor: 'text-[#F55600]' },
        warning: { bg: 'bg-white', border: 'border-amber-100', text: 'text-amber-950', icon: AlertTriangle, iconColor: 'text-amber-600' },
        success: { bg: 'bg-white', border: 'border-emerald-100', text: 'text-[#10B981]', icon: CheckCircle2, iconColor: 'text-[#10B981]' },
        danger: { bg: 'bg-white', border: 'border-rose-100', text: 'text-rose-950', icon: AlertCircle, iconColor: 'text-rose-600' },
        tip: { bg: 'bg-white', border: 'border-orange-100', text: 'text-orange-950', icon: Lightbulb, iconColor: 'text-[#F55600]' }
    };
    const s = styles[type] || styles.info;
    const Icon = s.icon;

    return (
        <div className={clsx("p-5 rounded-[24px] border-2 flex gap-4 text-sm my-6 shadow-sm", s.bg, s.border)}>
            <div className="shrink-0 mt-0.5"><Icon className={clsx("w-6 h-6", s.iconColor)} /></div>
            <div className="flex-1">
                {title && <div className={clsx("font-semibold uppercase tracking-wider mb-2", s.text)}>{title}</div>}
                <div className="text-[#2B2926] font-medium leading-relaxed">{children}</div>
            </div>
        </div>
    );
};

const DocList = ({ items, type = 'disc' }) => (
    <ul className="space-y-3 my-4">
        {items.map((item, i) => (
            <li key={i} className="flex items-start gap-3 text-sm font-medium text-[#2B2926] transition-colors hover:text-[#0f172A]">
                <span className="mt-1 shrink-0">
                    {type === 'check' && <CheckCircle2 className="w-4 h-4 text-emerald-500" />}
                    {type === 'arrow' && <ArrowRight className="w-4 h-4 text-[#F55600]" />}
                    {type === 'disc' && <div className="w-2 h-2 rounded-full bg-orange-200 mt-1.5" />}
                </span>
                <span className="leading-relaxed">{item}</span>
            </li>
        ))}
    </ul>
);

const UserGuide = () => {
    const [openSection, setOpenSection] = useState("welcome");

    const toggleSection = (id) => setOpenSection(openSection === id ? null : id);

    return (
        <div className="flex-1 bg-white overflow-y-auto custom-scrollbar animate-in slide-in-from-top-2" style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}>
            <div className="max-w-[1400px] mx-auto space-y-5 py-4 md:py-8 px-4 md:px-0 pb-24">

                {/* Header Card - Compact Sticky Refinement */}
                <div className="sticky top-0 z-50 bg-white/90 backdrop-blur-xl rounded-3xl p-4 border-2 border-[#2B2926]/30 shadow-[0_2px_8px_rgba(0,0,0,0.04)] mb-6">
                    <div className="flex items-center gap-4">
                        <div className="p-2.5 bg-gradient-to-br from-[#F55600] to-[#F55600] text-white rounded-xl shadow-lg shadow-orange-200 ring-2 ring-orange-50">
                            <BookOpen className="h-6 w-6" />
                        </div>
                        <div>
                            <p className="text-[9px] font-semibold uppercase tracking-[0.2em] text-[#F55600] mb-0.5">Documentation</p>
                            <h1 className="text-[18px] font-semibold text-[#0f172A] tracking-tight">
                                Pipelyt User Guide
                            </h1>
                        </div>
                    </div>
                </div>

                {/* 1. Welcome & Overview */}
                <AccordionItem isOpen={openSection === 'welcome'} onClick={() => toggleSection('welcome')}>
                    {(isOpen, onClick) => (
                        <>
                            <AccordionTrigger isOpen={isOpen} onClick={onClick}>
                                <div className="flex items-center gap-4">
                                    <div className="p-2.5 bg-white text-[#F55600] rounded-xl border-2 border-orange-100 shadow-sm">
                                        <Layout className="h-5 w-5" />
                                    </div>
                                    <div>
                                        <span className="font-semibold text-[#0f172A] text-lg block mb-0.5 tracking-tight">Welcome to Pipelyt</span>
                                        <span className="text-[#2B2926] text-[10px] font-semibold uppercase tracking-widest">Platform architecture & Core Agent workflow</span>
                                    </div>
                                </div>
                            </AccordionTrigger>
                            <AccordionContent isOpen={isOpen} className="p-6 md:p-10">
                                <p className="text-lg font-normal text-[#2B2926] leading-relaxed mb-8">
                                    Pipelyt is an autonomous Social Media Agent that unifies AI content generation, cross-platform publishing, and deep multi-modal analytics into one workspace. Connect LinkedIn, X, Facebook, and Instagram once — then brief the agent, generate platform-specific copy and visuals, schedule across all channels, and track performance from a single dashboard.
                                </p>

                                <DocSection title="The Agent Workflow" description="Go from a simple idea to a multi-platform campaign in four steps:">
                                    <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                                        {[
                                            { step: "01", title: "Brief", desc: "Tell the Agent what you want to post about." },
                                            { step: "02", title: "Generate", desc: "The AI crafts copy variants and visual assets." },
                                            { step: "03", title: "Review", desc: "Pick your favourite variants per platform." },
                                            { step: "04", title: "Deploy", desc: "Publish now or schedule across all channels." }
                                        ].map((item, i) => (
                                            <div key={i} className="p-6 rounded-[28px] border-2 border-orange-200 bg-white transition-all group">
                                                <div className="text-[10px] font-semibold text-[#10B981] mb-3 tracking-widest uppercase">{item.step}</div>
                                                <div className="font-semibold text-[#0f172A] mb-2 text-base tracking-tight">{item.title}</div>
                                                <div className="text-xs font-medium text-[#2B2926] leading-relaxed">{item.desc}</div>
                                            </div>
                                        ))}
                                    </div>
                                </DocSection>

                                <DocSection title="Powerful Capabilities" description="Your arsenal for total social media control:">
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                        <DocCallout type="info" title="Agentic Content Creation">
                                            Turn a one-sentence brief into platform-specific copy tailored for LinkedIn, X, Facebook, and Instagram, with AI-generated visuals that respect your brand DNA.
                                        </DocCallout>
                                        <DocCallout type="warning" title="Multi-Brand Workspace">
                                            Manage several companies and brand DNAs from one account. Filter every dashboard, chart, and table by company, brand, member, or location.
                                        </DocCallout>
                                        <DocCallout type="success" title="Reputation Agent">
                                            Auto-reply to incoming comments across LinkedIn, Facebook, Instagram, and X. Toggle the Agent ON to draft and post replies automatically with sentiment-aware tone.
                                        </DocCallout>
                                        <DocCallout type="tip" title="Deep Analytics">
                                            Track followers, engagement, reach, and engagement-rate over 24h / 7d / 30d / 90d / 1y / Custom windows. Drill into Performance Share by platform-account, see audience distribution, and break down individual post performance.
                                        </DocCallout>
                                        <DocCallout type="info" title="Schedule & Auto-Publish">
                                            Build a content calendar, schedule posts to the minute, and let Pipelyt publish exactly on time without you opening the app.
                                        </DocCallout>
                                        <DocCallout type="success" title="Team Collaboration">
                                            Agency and Enterprise plans support multi-member teams. Invite team members by email, assign brands and connections, and let everyone work in their own scoped view of the workspace.
                                        </DocCallout>
                                    </div>
                                </DocSection>
                            </AccordionContent>
                        </>
                    )}
                </AccordionItem>

                {/* 2. Getting Started */}
                <AccordionItem isOpen={openSection === 'getting-started'} onClick={() => toggleSection('getting-started')}>
                    {(isOpen, onClick) => (
                        <>
                            <AccordionTrigger isOpen={isOpen} onClick={onClick}>
                                <div className="flex items-center gap-4">
                                    <div className="p-2.5 bg-white text-emerald-600 rounded-xl border-2 border-emerald-100 shadow-sm">
                                        <Zap className="h-5 w-5" />
                                    </div>
                                    <div>
                                        <span className="font-semibold text-[#0f172A] text-lg block mb-0.5 tracking-tight">Quick Start Guide</span>
                                        <span className="text-[#2B2926] text-[10px] font-semibold uppercase tracking-widest">Onboarding, connections & your first campaign</span>
                                    </div>
                                </div>
                            </AccordionTrigger>
                            <AccordionContent isOpen={isOpen} className="p-6 md:p-10">
                                <DocSection title="1. Onboarding (Brand DNA setup)" description="Your first run through Pipelyt configures the AI for your brand voice.">
                                    <DocList type="arrow" items={[
                                        "Step 1 — Profile basics: full name, company name, timezone, role.",
                                        "Step 2 — Business URL: paste your website. Pipelyt scrapes it via Jina AI + Microlink to derive a Business DNA (tone, values, audience, products).",
                                        "Step 3 — Connect socials: pick LinkedIn, X, Facebook, Instagram (more on this below).",
                                        "Step 4 — Pricing plan: Starter, Growth, Agency, or Enterprise — billed via Stripe."
                                    ]} />
                                </DocSection>

                                <DocSection title="2. Connect Your Accounts" description="Pipelyt needs permission from each social network to publish posts and read analytics on your behalf.">
                                    <DocCallout type="warning" title="Account Permissions">
                                        Ensure you are logged into the Business or Page account you wish to connect. Personal Facebook profiles need a linked Page; Instagram requires a Business or Creator account linked to a Facebook Page.
                                    </DocCallout>
                                    <DocList type="arrow" items={[
                                        "Navigate to the 'Connections' tab in the sidebar.",
                                        "Click 'Connect' for each platform (LinkedIn, X, Facebook, Instagram).",
                                        "In the secure popup, grant the requested permissions.",
                                        "Select the specific pages or profiles you want to manage."
                                    ]} />
                                    <DocCallout type="info" title="Reddit & Other Platforms">
                                        Reddit is currently listed under "Coming Soon" — connect it when it goes live. The four core platforms (LinkedIn, X, Facebook, Instagram) are fully supported today.
                                    </DocCallout>
                                </DocSection>

                                <DocSection title="3. Your First Campaign" description="Use the Agent to generate content instantly from a brief.">
                                    <DocStep number="1" title="Define the Brief">
                                        Open the <strong>Create Campaign</strong> page (sidebar → the + icon, or the <strong>Create New Post</strong> action on the Dashboard). Enter what you want to talk about in the campaign brief box (e.g., "Launching our new sustainability feature this Friday"). Pick a Business DNA, aspect ratio, and the channels you want to target.
                                    </DocStep>
                                    <DocStep number="2" title="Generate Content">
                                        The multi-agent pipeline runs Brief Refine → Research → Copywriting → Visual generation. You'll get 3+ copy variants per platform and a unique image that reflects your Business DNA.
                                    </DocStep>
                                    <DocStep number="3" title="Fine-Tune & Deploy" isLast={true}>
                                        Review the variants. Edit copy directly, swap visuals, or open the image in Canva to refine it. Click <strong>Publish Now</strong> or <strong>Schedule</strong> to add it to your calendar.
                                    </DocStep>
                                </DocSection>
                            </AccordionContent>
                        </>
                    )}
                </AccordionItem>

                {/* 3. Brand Filter — NEW SECTION */}
                <AccordionItem isOpen={openSection === 'brand-filter'} onClick={() => toggleSection('brand-filter')}>
                    {(isOpen, onClick) => (
                        <>
                            <AccordionTrigger isOpen={isOpen} onClick={onClick}>
                                <div className="flex items-center gap-4">
                                    <div className="p-2.5 bg-white text-[#F55600] rounded-xl border-2 border-orange-100 shadow-sm">
                                        <Filter className="h-5 w-5" />
                                    </div>
                                    <div>
                                        <span className="font-semibold text-[#0f172A] text-lg block mb-0.5 tracking-tight">Brand Filter</span>
                                        <span className="text-[#2B2926] text-[10px] font-semibold uppercase tracking-widest">Cascading filter across every page</span>
                                    </div>
                                </div>
                            </AccordionTrigger>
                            <AccordionContent isOpen={isOpen} className="p-6 md:p-10">
                                <p className="text-base font-bold text-[#2B2926] leading-relaxed mb-8">
                                    The Brand Filter button appears in the top-right of the Dashboard, Analytics, Campaign Performance, Published, Scheduled, and Drafts pages. It scopes everything on the page to the company, brand, member, or location you pick — KPI cards, charts, audience breakdowns, and post lists all narrow together.
                                </p>

                                <DocSection title="Two layers of filters">
                                    <DocStep number="1" title="Always visible — Companies & Brands">
                                        <DocList type="check" items={[
                                            "Companies: a list combining your own company plus every team member's company. Picking one narrows visible_users to admins/members tagged with that company.",
                                            "Business DNA · Brands: each company's brand DNAs (e.g., a marketing arm with multiple sub-brands). Cascades from the picked company.",
                                        ]} />
                                    </DocStep>
                                    <DocStep number="2" title="Advanced (collapsible) — Members & Location" isLast={true}>
                                        <DocList type="check" items={[
                                            "Members: hand-pick individual team members instead of a whole company.",
                                            "Country / State / City / Pin Code: location-based slice — useful for agencies running localised campaigns.",
                                        ]} />
                                    </DocStep>
                                </DocSection>

                                <DocCallout type="info" title="What gets filtered">
                                    When you pick a company, the dashboard automatically narrows: KPI cards (followers, engagement, reach), the Performance Trends chart, the Audience Distribution donut, the Platform Performance breakdown, and the post tables on Published / Scheduled / Drafts. Each chart has its own per-card company dropdown for finer drill-down.
                                </DocCallout>

                                <DocCallout type="tip" title="Stale-while-revalidate caching">
                                    Toggling between filter combinations you've already viewed paints instantly — Pipelyt caches each filter signature in memory and on disk. Fresh data still loads silently in the background, so the UI never makes you wait.
                                </DocCallout>
                            </AccordionContent>
                        </>
                    )}
                </AccordionItem>

                {/* 4. Campaign Builder */}
                <AccordionItem isOpen={openSection === 'builder'} onClick={() => toggleSection('builder')}>
                    {(isOpen, onClick) => (
                        <>
                            <AccordionTrigger isOpen={isOpen} onClick={onClick}>
                                <div className="flex items-center gap-4">
                                    <div className="p-2.5 bg-white text-purple-600 rounded-xl border-2 border-purple-100 shadow-sm">
                                        <PlusCircle className="h-5 w-5" />
                                    </div>
                                    <div>
                                        <span className="font-semibold text-[#0f172A] text-lg block mb-0.5 tracking-tight">Campaign Builder</span>
                                        <span className="text-[#2B2926] text-[10px] font-semibold uppercase tracking-widest">Mastering Image, Video, and Document posts</span>
                                    </div>
                                </div>
                            </AccordionTrigger>
                            <AccordionContent isOpen={isOpen} className="p-6 md:p-10">
                                <DocSection title="Manual Mode Selection" description="Pick a content type for tailored distribution.">
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
                                        <div className="p-5 border-2 border-[#2B2926]/30 rounded-[28px] bg-white shadow-sm">
                                            <div className="font-semibold text-[#2B2926] mb-3 flex items-center gap-3">
                                                <div className="w-8 h-8 rounded-xl bg-white border border-[#2B2926]/30 flex items-center justify-center">
                                                    <Type className="w-4 h-4 text-[#F55600]" />
                                                </div>
                                                Text Post
                                            </div>
                                            <p className="text-sm font-bold text-[#2B2926] leading-relaxed mb-4">
                                                Share thoughts and quick updates. Supports X threads and LinkedIn long-form posts (3000+ characters).
                                            </p>
                                        </div>

                                        <div className="p-5 border-2 border-[#2B2926]/30 rounded-[28px] bg-white shadow-sm">
                                            <div className="font-semibold text-[#2B2926] mb-3 flex items-center gap-3">
                                                <div className="w-8 h-8 rounded-xl bg-white border border-[#2B2926]/30 flex items-center justify-center">
                                                    <ImageIcon className="w-4 h-4 text-[#0A66C2]" />
                                                </div>
                                                Image Post
                                            </div>
                                            <p className="text-sm font-bold text-[#2B2926] leading-relaxed mb-4">
                                                Upload single or multiple images. Integrates with Canva for professional-grade design without leaving the app.
                                            </p>
                                        </div>

                                        <div className="p-5 border-2 border-[#2B2926]/30 rounded-[28px] bg-white shadow-sm">
                                            <div className="font-semibold text-[#2B2926] mb-3 flex items-center gap-3">
                                                <div className="w-8 h-8 rounded-xl bg-white border border-[#2B2926]/30 flex items-center justify-center">
                                                    <Video className="w-4 h-4 text-purple-600" />
                                                </div>
                                                Video Post
                                            </div>
                                            <p className="text-sm font-bold text-[#2B2926] leading-relaxed mb-4">
                                                Optimised for Reels, Shorts, and X video (up to 140s on free-tier accounts).
                                            </p>
                                        </div>

                                        <div className="p-5 border-2 border-[#2B2926]/30 rounded-[28px] bg-white shadow-sm">
                                            <div className="font-semibold text-[#2B2926] mb-3 flex items-center gap-3">
                                                <div className="p-1 rounded-lg text-[#2B2926] transition-all flex items-center justify-center shrink-0 border border-transparent shadow-sm">
                                                    <FileText className="w-4 h-4 text-[#10B981]" />
                                                </div>
                                                Document Post (LinkedIn)
                                            </div>
                                             <p className="text-sm font-medium text-[#2B2926] leading-relaxed mb-4">
                                                Exclusive to LinkedIn. Upload a PDF to create an interactive document carousel — the highest-engagement format on LinkedIn.
                                            </p>
                                        </div>
                                    </div>
                                </DocSection>

                                <DocCallout type="tip" title="Pro-Tip: Canva Integration">
                                    Click <strong>Design with Canva</strong> inside Image Mode to open your Canva projects. Once you hit Publish in Canva, the design automatically teleports back into Pipelyt for deployment.
                                </DocCallout>
                            </AccordionContent>
                        </>
                    )}
                </AccordionItem>

                {/* 4b. Content Creation Rules & Validation — Every guardrail
                     you'll hit while composing, publishing, or scheduling.
                     Mirrors the behaviour of the actual toasts / modals so
                     users know exactly what to do when something is blocked. */}
                <AccordionItem isOpen={openSection === 'rules'} onClick={() => toggleSection('rules')}>
                    {(isOpen, onClick) => (
                        <>
                            <AccordionTrigger isOpen={isOpen} onClick={onClick}>
                                <div className="flex items-center gap-4">
                                    <div className="p-2.5 bg-white text-amber-600 rounded-xl border-2 border-amber-100 shadow-sm">
                                        <Shield className="h-5 w-5" />
                                    </div>
                                    <div>
                                        <span className="font-semibold text-[#0f172A] text-lg block mb-0.5 tracking-tight">Content Creation Rules</span>
                                        <span className="text-[#2B2926] text-[10px] font-semibold uppercase tracking-widest">Validation, guardrails, and per-platform requirements</span>
                                    </div>
                                </div>
                            </AccordionTrigger>
                            <AccordionContent isOpen={isOpen} className="p-6 md:p-10">

                                <DocSection title="Adding your brand logo" description="Upload once in Profile → Business DNA, and it appears on every image the AI generates.">
                                    <DocList type="arrow" items={[
                                        "Upload an image file — PNG, JPG, SVG, or WEBP. PDFs or other file types will be rejected with a message.",
                                        "Keep the file under 5 MB.",
                                        "Pick a Business DNA on the Create Campaign page, and the AI uses that brand's logo on every generated image. Regenerate and Custom Prompt use the same logo.",
                                    ]} />
                                </DocSection>

                                <DocSection title="Writing a good campaign brief" description="Some briefs are automatically rejected so you don't waste time waiting on results you won't use.">
                                    <DocList type="arrow" items={[
                                        "The brief cannot be empty.",
                                        "Placeholders like \"create a post\" or \"write something\" won't be accepted — describe what you actually want to share.",
                                        "Requests for harmful, illegal, or off-topic content are rejected with a short explanation.",
                                        "Your brief needs actual words — an emoji-only prompt won't work.",
                                    ]} />
                                    <DocCallout type="tip" title="What a strong brief looks like">
                                        Tell the AI: who you're talking to, what you're announcing or teaching, and one concrete detail (a stat, a feature name, a date). Example: <em>"Announce our new Zapier integration for marketing teams — highlight that it saves ~4 hours a week on reporting. Launch Friday."</em>
                                    </DocCallout>
                                </DocSection>

                                <DocSection title="Requirements per platform" description="Some channels need extra fields before a post can go out.">
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                        <div className="p-4 border-2 border-[#2B2926]/30 rounded-3xl bg-white">
                                            <div className="flex items-center gap-2 mb-2 font-semibold text-[#0f172A] text-sm">
                                                <ImageIcon className="w-4 h-4 text-pink-500" /> Instagram
                                            </div>
                                            <p className="text-[11px] font-medium text-[#2B2926]">Every Instagram post needs an image or video. Text-only posts to Instagram are not supported by Instagram itself.</p>
                                        </div>
                                        <div className="p-4 border-2 border-[#2B2926]/30 rounded-3xl bg-white">
                                            <div className="flex items-center gap-2 mb-2 font-semibold text-[#0f172A] text-sm">
                                                <Video className="w-4 h-4 text-red-500" /> YouTube
                                            </div>
                                            <p className="text-[11px] font-medium text-[#2B2926]">A video file and a title are required. You can optionally add tags, choose a category, and set privacy (public / unlisted / private).</p>
                                        </div>
                                        <div className="p-4 border-2 border-[#2B2926]/30 rounded-3xl bg-white">
                                            <div className="flex items-center gap-2 mb-2 font-semibold text-[#0f172A] text-sm">
                                                <Video className="w-4 h-4 text-purple-600" /> TikTok
                                            </div>
                                            <p className="text-[11px] font-medium text-[#2B2926]">A video is required. Privacy defaults to <strong>Only me</strong> — switch to <strong>Public</strong> if you want the video visible to everyone.</p>
                                        </div>
                                        <div className="p-4 border-2 border-[#2B2926]/30 rounded-3xl bg-white">
                                            <div className="flex items-center gap-2 mb-2 font-semibold text-[#0f172A] text-sm">
                                                <FileText className="w-4 h-4 text-[#10B981]" /> Pinterest
                                            </div>
                                            <p className="text-[11px] font-medium text-[#2B2926]">Every pin needs an image. Text-only pins are not supported.</p>
                                        </div>
                                    </div>
                                    <DocCallout type="info" title="Video posts">
                                        In Video mode, the Publish button stays disabled until both your caption and video have finished uploading. If it's still greyed out, wait a moment — the video is likely still uploading.
                                    </DocCallout>
                                </DocSection>

                                <DocSection title="Live publishing status" description="Every time you hit Publish, a status window shows what's happening on each channel.">
                                    <DocList type="arrow" items={[
                                        "One row per selected platform. A spinner is shown while publishing is in progress.",
                                        "Each row updates to ✅ when the platform accepts the post, ❌ if it fails, or ⚠️ if some accounts succeeded while others didn't.",
                                        "The window stays open until every platform has finished — don't refresh the page while it's running.",
                                        "You'll see a final summary at the top, like \"Published to all 4 platforms\" or \"2 of 4 succeeded\".",
                                    ]} />
                                </DocSection>

                                <DocSection title="Scheduling a multi-day campaign" description="On the Create Campaign page, switch the strategy from Publish Now to Schedule to plan several days of posts at once.">
                                    <DocList type="arrow" items={[
                                        "Plan Duration accepts between 1 and 30 days. Zero, blank, or negative values will show an error.",
                                        "You still need a campaign brief and at least one selected channel.",
                                        "The AI proposes one post per day, with a date, time, and topic. You review each one before it goes live.",
                                    ]} />
                                    <DocCallout type="info" title="Review & Approve queue">
                                        Every generated slot appears in the queue with a proposed schedule. For each post you can: <strong>Edit</strong> to change the caption or move it to a different future time, <strong>Approve</strong> to add it to your Scheduled tab, or <strong>Reject</strong> to remove it. Use <strong>Approve All</strong> to send everything to Scheduled in one click.
                                    </DocCallout>
                                </DocSection>

                                <DocSection title="Rescheduling a post" description="Editing a scheduled post's time follows one simple rule.">
                                    <DocList type="arrow" items={[
                                        "You can only reschedule to a time in the future. Picking a past time will show an error and the post stays where it was.",
                                        "Times are always shown in the timezone you picked in Settings, so what you type is what the post will go out at.",
                                    ]} />
                                </DocSection>

                                <DocSection title="Choosing an image style" description="Use the Style chip on the Create Campaign page to lock the visual look for every generated image.">
                                    <DocList type="arrow" items={[
                                        "Auto (default) — the AI picks a style that matches your Business DNA and industry. Same behavior as before this feature existed.",
                                        "Physical Product styles — Studio Product Shot, Lifestyle / On-Body, Flat Lay, Macro Close-up, 3D Product Render, Cinematic. Use these when you're selling something physical (fashion, jewelry, food, gadgets).",
                                        "Service & SaaS styles — Photorealistic, Infographic, Illustration, Isometric, UI Mockup / Device Frame. Use these for thought-leadership, feature announcements, or data-driven posts.",
                                        "Creative styles — Minimalist Flat, Cartoon, Watercolor, Cyberpunk / Neon. Use these to stand out in the feed or match a playful / artisan / futuristic brand voice.",
                                        "When you pick any style other than Auto, that style becomes the dominant directive — the campaign brief and post copy tell the AI WHAT to render, but your style choice controls HOW it looks.",
                                        "The Style chip is hidden for Text-only campaigns (nothing to style).",
                                    ]} />
                                    <DocCallout type="info" title="Your logo is protected">
                                        Even when you pick a stylized look (Cartoon, Watercolor, etc.), your brand logo is rendered exactly as you uploaded it in Business DNA. Only the surrounding scene adopts the chosen aesthetic.
                                    </DocCallout>
                                </DocSection>

                                <DocSection title="Regenerate & Custom Prompt" description="Two ways to get a different image after your first Agent Post generation.">
                                    <DocList type="arrow" items={[
                                        "Regenerate: creates two brand-new variants from the same brief. Useful when the first set doesn't quite land.",
                                        "Custom Prompt: opens a text box so you can describe exactly what you want the image to look like, in your own words. The AI uses your text as-is.",
                                        "Both use the Business DNA you picked — same brand logo, same brand colours.",
                                    ]} />
                                </DocSection>

                                <DocSection title="How previews look" description="Drafts, Scheduled, Published, and Calendar all show your posts the same way.">
                                    <DocList type="arrow" items={[
                                        "PDF carousels show a real preview of the first slide, not a generic PDF icon. Works for both AI-generated carousels and PDFs you upload yourself.",
                                        "Video posts show a playable preview you can hit play on. For YouTube posts, you'll see the YouTube thumbnail with a play button.",
                                        "Image posts show the image, with a small fallback graphic if the image can't be loaded for any reason.",
                                    ]} />
                                </DocSection>

                                <DocSection title="Confirmations before you delete" description="Delete and reject actions always ask you to confirm — in a Pipelyt-styled dialog, not a plain browser popup.">
                                    <DocList type="arrow" items={[
                                        "Deleting a draft, a scheduled post, or a published post.",
                                        "Rejecting a slot in the multi-day plan review queue.",
                                        "Cancelling a scheduled post from the Calendar.",
                                        "You'll always see a clear question and Cancel + Confirm buttons before anything is removed.",
                                    ]} />
                                </DocSection>

                            </AccordionContent>
                        </>
                    )}
                </AccordionItem>

                {/* 5. Analytics & Insights — REWRITTEN */}
                <AccordionItem isOpen={openSection === 'analytics'} onClick={() => toggleSection('analytics')}>
                    {(isOpen, onClick) => (
                        <>
                            <AccordionTrigger isOpen={isOpen} onClick={onClick}>
                                <div className="flex items-center gap-4">
                                    <div className="p-2.5 bg-blue-50 text-blue-600 rounded-xl border-2 border-blue-100 group-hover:bg-white transition-all shadow-sm">
                                        <BarChart3 className="h-5 w-5" />
                                    </div>
                                    <div>
                                        <span className="font-semibold text-[#0f172A] text-lg block mb-0.5 tracking-tight">Analytics & Insights</span>
                                        <span className="text-[#2B2926] text-[10px] font-semibold uppercase tracking-widest">KPI ribbon, charts, performance share & sentiment</span>
                                    </div>
                                </div>
                            </AccordionTrigger>
                            <AccordionContent isOpen={isOpen} className="p-6 md:p-10">
                                <DocSection title="The Analytics Dashboard layout" description="Top to bottom, the dashboard surfaces follower growth, engagement trends, audience composition, and per-platform performance.">
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
                                        <div className="p-5 border-2 border-orange-100 rounded-3xl bg-white">
                                            <div className="flex items-center gap-2 mb-3">
                                                <TrendingUp className="w-4 h-4 text-[#F55600]" />
                                                <span className="font-semibold text-[#0f172A] text-sm">KPI ribbon</span>
                                            </div>
                                            <p className="text-[12px] font-medium text-[#2B2926] leading-relaxed">Total Followers, Total Engagement, Total Reach, Average Engagement Rate. Each card shows a comparison vs. the previous period.</p>
                                        </div>
                                        <div className="p-5 border-2 border-orange-100 rounded-3xl bg-white">
                                            <div className="flex items-center gap-2 mb-3">
                                                <Activity className="w-4 h-4 text-[#F55600]" />
                                                <span className="font-semibold text-[#0f172A] text-sm">Performance Trends & Follower Growth</span>
                                            </div>
                                            <p className="text-[12px] font-medium text-[#2B2926] leading-relaxed">Combined area + line chart. Engagement, Likes, Comments, Shares, Reach on the left axis; Follower Growth % on the right axis. Toggle metrics from the legend.</p>
                                        </div>
                                        <div className="p-5 border-2 border-orange-100 rounded-3xl bg-white">
                                            <div className="flex items-center gap-2 mb-3">
                                                <PieChart className="w-4 h-4 text-[#F55600]" />
                                                <span className="font-semibold text-[#0f172A] text-sm">Audience Distribution</span>
                                            </div>
                                            <p className="text-[12px] font-medium text-[#2B2926] leading-relaxed">Donut chart showing follower share per platform-account, with platform-brand colours. Multiple companies on the same platform get progressive shades for visual distinction.</p>
                                        </div>
                                        <div className="p-5 border-2 border-orange-100 rounded-3xl bg-white">
                                            <div className="flex items-center gap-2 mb-3">
                                                <Tag className="w-4 h-4 text-[#F55600]" />
                                                <span className="font-semibold text-[#0f172A] text-sm">Platform Performance</span>
                                            </div>
                                            <p className="text-[12px] font-medium text-[#2B2926] leading-relaxed">Per-account ranked list — followers, Performance Share %, and a horizontal bar visualising each account's contribution to total engagement.</p>
                                        </div>
                                    </div>
                                </DocSection>

                                <DocSection title="Time period & filter controls" description="The top bar drives every chart and KPI on the page.">
                                    <DocList type="arrow" items={[
                                        "Time period: 24h, 7d, 30d, or Custom (date range). The chart x-axis and KPI deltas all respect this window.",
                                        "Platform: All / LinkedIn / X / Facebook / Instagram — filters every chart to one platform's accounts only.",
                                        "Brand Filter: pick a company / brand / member / location to slice the entire dashboard.",
                                        "Refresh: pulls the latest follower counts and post metrics from every connected channel. It usually takes about a minute to complete.",
                                        "Export: downloads a PDF report including the KPI ribbon, charts, and platform breakdowns.",
                                    ]} />
                                </DocSection>

                                <DocSection title="Performance Share — what it actually means" description="The metric next to each row in Platform Performance, often misunderstood.">
                                    <DocCallout type="info" title="Engagement-share, not follower-share">
                                        Performance Share = (this account's engagement) / (total engagement across all accounts) × 100. Engagement here is the sum of likes + comments + shares + reactions on the account's posts in the selected window. It tells you which account is producing the most interactions, regardless of how big its follower base is.
                                    </DocCallout>
                                    <DocList type="check" items={[
                                        "Always sums to ~100% across the rows shown — it's a share, not a per-row score.",
                                        "Decoupled from followers — a small account can hold a big share if its posts land well.",
                                        "Time-window-sensitive — switching 24h ↔ 7d ↔ 30d changes both numerator and denominator.",
                                        "High follower count + low Performance Share = audience exists but isn't engaging → reshape content.",
                                        "Low followers + high Performance Share = high-quality audience → invest in growth.",
                                    ]} />
                                </DocSection>

                                <DocSection title="Per-graph company filters" description="Each chart card has its own dropdown for one-click drill-down without changing the global view.">
                                    <DocList type="arrow" items={[
                                        "Performance Trends & Follower Growth: pick a single company to re-fetch the chart narrowed to just that company's accounts and assigned brand DNAs.",
                                        "Audience Distribution: filters the donut chart and legend to one company at a time, so a multi-company workspace can isolate each brand's audience composition.",
                                    ]} />
                                </DocSection>

                                <DocSection title="AI Sentiment Analysis" description="Pipelyt doesn't just count comments — it reads them.">
                                    <DocStep number="1" title="Trigger a Deep Sentiment Scan" isLast={true}>
                                        Click the Semantic Score column on any post in Campaign Performance. The Agent fetches every comment, classifies tone, and outputs an overall score:
                                        <div className="flex gap-2 mt-3">
                                            <span className="px-2.5 py-1 bg-emerald-100 text-emerald-700 text-[9px] font-semibold uppercase rounded-lg border border-emerald-200">Positive</span>
                                            <span className="px-2.5 py-1 bg-orange-100 text-orange-700 text-[9px] font-semibold uppercase rounded-lg border border-orange-200">Neutral</span>
                                            <span className="px-2.5 py-1 bg-red-100 text-red-700 text-[9px] font-semibold uppercase rounded-lg border border-red-200">Negative</span>
                                        </div>
                                        <p className="text-xs text-[#2B2926] mt-3">Sentiment requires a minimum of 5 comments on a post for accuracy. Below that threshold the Agent reports "insufficient data".</p>
                                    </DocStep>
                                </DocSection>
                            </AccordionContent>
                        </>
                    )}
                </AccordionItem>

                {/* 6. Campaign Performance — NEW SECTION */}
                <AccordionItem isOpen={openSection === 'campaign-performance'} onClick={() => toggleSection('campaign-performance')}>
                    {(isOpen, onClick) => (
                        <>
                            <AccordionTrigger isOpen={isOpen} onClick={onClick}>
                                <div className="flex items-center gap-4">
                                    <div className="p-2.5 bg-white text-emerald-600 rounded-xl border-2 border-emerald-100 shadow-sm">
                                        <Activity className="h-5 w-5" />
                                    </div>
                                    <div>
                                        <span className="font-semibold text-[#0f172A] text-lg block mb-0.5 tracking-tight">Campaign Performance</span>
                                        <span className="text-[#2B2926] text-[10px] font-semibold uppercase tracking-widest">Per-post metrics table</span>
                                    </div>
                                </div>
                            </AccordionTrigger>
                            <AccordionContent isOpen={isOpen} className="p-6 md:p-10">
                                <p className="text-base font-bold text-[#2B2926] leading-relaxed mb-6">
                                    Campaign Performance is a dedicated tab in the sidebar that surfaces only the post-level performance table — no KPI ribbon, no charts, no audience breakdown. Use it when you want a focused, sortable view of every published post and its metrics.
                                </p>

                                <DocSection title="Columns">
                                    <DocList type="check" items={[
                                        "Content — copy preview + image thumbnail (click to enlarge fullscreen).",
                                        "Preview — eye icon to open the full preview modal with the live LinkedIn / Facebook / Instagram / X mock-up.",
                                        "Platform — colour-coded platform icon.",
                                        "Likes / Comments / Shares / Reach / Engagement — coloured pill counts.",
                                        "Semantic Score — click to trigger the AI sentiment scan."
                                    ]} />
                                </DocSection>

                                <DocCallout type="tip" title="Refresh is fast here">
                                    Refresh on the Campaign Performance page only re-loads the post list — not the full sync from every channel — so it comes back almost immediately. Coming back to the tab later paints instantly from the last-loaded view.
                                </DocCallout>

                                <DocSection title="Post preview — always up to date" description="Click the eye icon on any row to open the full post preview.">
                                    <DocList type="arrow" items={[
                                        "The Likes / Comments / Shares / Reach tiles update automatically the moment the preview opens — you don't need to hit Refresh yourself.",
                                        "Numbers are shown for the specific channel you clicked. If the same post is live on Instagram, Facebook, X, and LinkedIn, opening the Instagram row shows only Instagram's numbers — not the total across all channels.",
                                        "A small \"Syncing…\" tag appears while Pipelyt checks each channel for the newest counts. Click Refresh again once it clears to see any changes.",
                                    ]} />
                                    <DocCallout type="info" title="Comments">
                                        Below the stats you'll see the actual comments on the post. Use the Refresh button in the Comments section to pull the latest ones. Reply directly from Pipelyt — your reply appears right away and posts to the platform. Turn on <strong>Agent Post</strong> to have the AI suggest a reply for each unanswered comment.
                                    </DocCallout>
                                </DocSection>
                            </AccordionContent>
                        </>
                    )}
                </AccordionItem>

                {/* 7. Reputation — NEW SECTION */}
                <AccordionItem isOpen={openSection === 'reputation'} onClick={() => toggleSection('reputation')}>
                    {(isOpen, onClick) => (
                        <>
                            <AccordionTrigger isOpen={isOpen} onClick={onClick}>
                                <div className="flex items-center gap-4">
                                    <div className="p-2.5 bg-white text-[#F55600] rounded-xl border-2 border-orange-100 shadow-sm">
                                        <MessageSquare className="h-5 w-5" />
                                    </div>
                                    <div>
                                        <span className="font-semibold text-[#0f172A] text-lg block mb-0.5 tracking-tight">Reputation & Community</span>
                                        <span className="text-[#2B2926] text-[10px] font-semibold uppercase tracking-widest">Auto-reply Agent & community management</span>
                                    </div>
                                </div>
                            </AccordionTrigger>
                            <AccordionContent isOpen={isOpen} className="p-6 md:p-10">
                                <p className="text-base font-bold text-[#2B2926] leading-relaxed mb-6">
                                    The Reputation tab is your central hub for managing every comment that lands on your posts across LinkedIn, X, Facebook, and Instagram. Pick a post on the left to see its full comment thread on the right, then reply manually or let the AI Agent handle it automatically.
                                </p>

                                <DocSection title="Layout">
                                    <DocList type="arrow" items={[
                                        "Left panel: list of every published post that has at least one comment, sorted by comment count (descending) by default.",
                                        "Filters above the list: All Platforms / LinkedIn / Facebook / Instagram / X, plus All Time / Past Week / Past Month / Past Year.",
                                        "Right panel: opens the selected post's threaded comments with reply box, AI suggested reply, and the Auto-Reply Agent toggle.",
                                    ]} />
                                </DocSection>

                                <DocSection title="Auto-Reply Agent" description="Toggle ON in the top-right of the comment panel to let the AI manage your replies.">
                                    <DocStep number="1" title="What happens when ON">
                                        The Agent auto-generates a context-aware reply for every unanswered root comment using the post's content + brand DNA tone. Replies post automatically — no Generate button to click. As new comments arrive (during a session), the Agent picks them up too.
                                    </DocStep>
                                    <DocStep number="2" title="What stays manual">
                                        Already-answered comments are skipped. Comments where you've started typing in the reply box are skipped (so you don't lose in-progress drafts).
                                    </DocStep>
                                    <DocStep number="3" title="Toggle OFF" isLast={true}>
                                        Flip the Agent OFF and you're back to fully manual replies. AI suggestions still appear next to each comment with a one-click "Use this reply" option.
                                    </DocStep>
                                </DocSection>

                                <DocCallout type="warning" title="Auto-reply uses your plan quota">
                                    Each auto-generated reply uses a small part of your monthly AI quota. For high-traffic posts, plan accordingly or turn the Agent off and reply selectively.
                                </DocCallout>
                            </AccordionContent>
                        </>
                    )}
                </AccordionItem>

                {/* 8. Scheduling & Calendar */}
                <AccordionItem isOpen={openSection === 'calendar'} onClick={() => toggleSection('calendar')}>
                    {(isOpen, onClick) => (
                        <>
                            <AccordionTrigger isOpen={isOpen} onClick={onClick}>
                                <div className="flex items-center gap-4">
                                    <div className="p-2.5 bg-pink-50 text-pink-600 rounded-xl border-2 border-pink-100 group-hover:bg-white transition-all shadow-sm">
                                        <Calendar className="h-5 w-5" />
                                    </div>
                                    <div>
                                        <span className="font-semibold text-[#0f172A] text-lg block mb-0.5 tracking-tight">Scheduling & Calendar</span>
                                        <span className="text-[#2B2926] text-[10px] font-semibold uppercase tracking-widest">Mastering your content pipeline</span>
                                    </div>
                                </div>
                            </AccordionTrigger>
                            <AccordionContent isOpen={isOpen} className="p-6 md:p-10">
                                <DocSection title="The Content Pipeline" description="Manage what's going live, and when.">
                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                                        <div className="p-4 border-2 border-[#2B2926]/30 rounded-3xl bg-white">
                                            <div className="flex items-center gap-2 mb-2">
                                                <RotateCcw className="w-4 h-4 text-orange-500" />
                                                <span className="font-semibold text-[#0f172A] text-sm">Drafts</span>
                                            </div>
                                            <p className="text-[11px] font-medium text-[#2B2926]">Unfinished or pending review. Access via the Drafts tab.</p>
                                        </div>
                                        <div className="p-4 border-2 border-[#2B2926]/30 rounded-3xl bg-white">
                                            <div className="flex items-center gap-2 mb-2">
                                                <Clock className="w-4 h-4 text-purple-500" />
                                                <span className="font-semibold text-[#0f172A] text-sm">Scheduled</span>
                                            </div>
                                            <p className="text-[11px] font-medium text-[#2B2926]">Queued for the future. Cancel or shift in the Scheduled tab.</p>
                                        </div>
                                        <div className="p-4 border-2 border-[#2B2926]/30 rounded-3xl bg-white">
                                            <div className="flex items-center gap-2 mb-2">
                                                <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                                                <span className="font-semibold text-[#0f172A] text-sm">Published</span>
                                            </div>
                                            <p className="text-[11px] font-medium text-[#2B2926]">Successfully live. Inspect performance in the Published tab.</p>
                                        </div>
                                    </div>
                                </DocSection>

                                <DocSection title="Calendar view">
                                    <DocList type="arrow" items={[
                                        "Month and Week views. Toggle in the top centre.",
                                        "Each day cell shows platform icons + a count badge for posts scheduled / published that day.",
                                        "Click a cell with posts to open the day's modal — full post list, click any to drill into the preview / repost / cancel actions.",
                                        "Hover any cell to reveal the orange + button — drops a fresh scheduling form for that day.",
                                    ]} />
                                </DocSection>

                                <DocCallout type="warning" title="Posts publish automatically at the scheduled time">
                                    You don't need to keep Pipelyt open. As long as your account is active and your connected channels are still linked, scheduled posts go live within a minute of the time you picked.
                                </DocCallout>
                            </AccordionContent>
                        </>
                    )}
                </AccordionItem>

                {/* 9. Team Collaboration — REWRITTEN */}
                <AccordionItem isOpen={openSection === 'team'} onClick={() => toggleSection('team')}>
                    {(isOpen, onClick) => (
                        <>
                            <AccordionTrigger isOpen={isOpen} onClick={onClick}>
                                <div className="flex items-center gap-4">
                                    <div className="p-2.5 bg-indigo-50 text-indigo-600 rounded-xl border-2 border-indigo-100 group-hover:bg-white transition-all shadow-sm">
                                        <Users className="h-5 w-5" />
                                    </div>
                                    <div>
                                        <span className="font-semibold text-[#0f172A] text-lg block mb-0.5 tracking-tight">Team Collaboration</span>
                                        <span className="text-[#2B2926] text-[10px] font-semibold uppercase tracking-widest">Inviting members, brand assignment & connections</span>
                                    </div>
                                </div>
                            </AccordionTrigger>
                            <AccordionContent isOpen={isOpen} className="p-6 md:p-10">
                                <p className="text-base font-bold text-[#2B2926] leading-relaxed mb-6">
                                    Agency and Enterprise plans support multi-member teams. The Members tab shows seat usage, accepted members, and pending invites. Admins manage everything; members operate inside the brands and connections they've been granted.
                                </p>

                                <DocSection title="Invite a team member">
                                    <DocStep number="1" title="Click 'Invite Member'">
                                        Opens the centred invite modal. Required fields: <strong>Email</strong>, <strong>Name (for your reference)</strong>, and at least one <strong>Assigned Brand</strong>. Each is marked with an orange asterisk.
                                    </DocStep>
                                    <DocStep number="2" title="Optional: Company & Location">
                                        Click the chevron to expand. Set the member's company name (used by the Brand Filter), country, state/region, city, and pin/ZIP. All optional — leave blank if not relevant.
                                    </DocStep>
                                    <DocStep number="3" title="Optional: Pre-assign Connections">
                                        Click the chevron next to <strong>Connections</strong> to expand. Each available account is shown with its platform icon and name (e.g., "LinkedIn · Z-Ninth"), so you can tell duplicates apart at a glance. Tick the boxes to delegate accounts immediately on accept.
                                    </DocStep>
                                    <DocStep number="4" title="Send Invite" isLast={true}>
                                        A success popup appears center-screen. The member receives an email with a single-use accept link. If email delivery fails, the popup falls back to a Copy-Link button so you can share manually.
                                    </DocStep>
                                </DocSection>

                                <DocSection title="Resend, Revoke, Disable, Delete">
                                    <DocList type="arrow" items={[
                                        "Resend (pending invites): generates a fresh accept link, sends a new invite email, and copies the link to your clipboard. You'll see a confirmation message at the bottom of the screen.",
                                        "Revoke (pending invites): cancels the invite immediately after you confirm.",
                                        "Disable (accepted members): blocks login but preserves all their content for the historical record.",
                                        "Delete (accepted members): permanently removes the member; their content stays with you, their delegated accounts return to your pool.",
                                    ]} />
                                </DocSection>

                                <DocSection title="Roles">
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
                                        <div className="p-4 border-2 border-[#2B2926]/30 rounded-2xl bg-white">
                                            <div className="font-semibold text-[#0f172A] text-sm mb-1 uppercase tracking-tight">Admin</div>
                                            <p className="text-xs font-medium text-[#2B2926]">Full access to billing, team management, brand DNA, and every connection. Sees the global Brand Filter on every page.</p>
                                        </div>
                                        <div className="p-4 border-2 border-[#2B2926]/30 rounded-2xl bg-white">
                                            <div className="font-semibold text-[#0f172A] text-sm mb-1 uppercase tracking-tight">Member</div>
                                            <p className="text-xs font-medium text-[#2B2926]">Sees only the brands and connections delegated to them. Can create, schedule, and reply to comments inside that scope. Cannot manage billing, invites, or other members.</p>
                                        </div>
                                    </div>
                                </DocSection>
                            </AccordionContent>
                        </>
                    )}
                </AccordionItem>

                {/* 10. Settings & Security */}
                <AccordionItem isOpen={openSection === 'settings'} onClick={() => toggleSection('settings')}>
                    {(isOpen, onClick) => (
                        <>
                            <AccordionTrigger isOpen={isOpen} onClick={onClick}>
                                <div className="flex items-center gap-4">
                                    <div className="p-2.5 bg-slate-100 text-[#2B2926] rounded-xl border-2 border-[#2B2926]/30 group-hover:bg-white transition-all shadow-sm">
                                        <Settings className="h-5 w-5" />
                                    </div>
                                    <div>
                                        <span className="font-semibold text-[#0f172A] text-lg block mb-0.5 tracking-tight">Account & Security</span>
                                        <span className="text-[#2B2926] text-[10px] font-semibold uppercase tracking-widest">Profile, brand DNA, billing & password</span>
                                    </div>
                                </div>
                            </AccordionTrigger>
                            <AccordionContent isOpen={isOpen} className="p-6 md:p-10">
                                <DocSection title="Workspace Configuration" description="Ensure your Agent operates in the right context.">
                                    <DocCallout type="warning" title="Critical: Timezone Settings">
                                        All scheduling is based on your account's timezone. Set this correctly in <strong>Settings</strong> so posts go live at the time you actually meant.
                                    </DocCallout>
                                    <DocList type="check" items={[
                                        "Update full name, company name, and brand mark logo.",
                                        "Change your business URL — Pipelyt will re-derive your Business DNA from the new site (the old DNA is retained until the new fetch succeeds).",
                                        "Reset your password anytime under the Security section (OTP verification by email).",
                                        "Manage your subscription, payment method, and invoices in Billing (Admins only).",
                                    ]} />
                                </DocSection>
                            </AccordionContent>
                        </>
                    )}
                </AccordionItem>

            </div>
        </div>
    );
};

export default UserGuide;
