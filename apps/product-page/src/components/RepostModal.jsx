import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Send, Calendar, Sparkles, AlertCircle, CheckCircle2, Loader2, Image as ImageIcon, PlayCircle, ArrowRight, ArrowLeft, Check } from 'lucide-react';
import RadialClockPicker from './CustomTimePicker';
import PlatformLogo from './PlatformLogo';

const RepostModal = ({ 
  isOpen, 
  onClose, 
  post, 
  authAxios, 
  user,
  setMessage,
  fetchPublished,
  fetchScheduled
}) => {
  const [step, setStep] = useState(1); // 1: Review, 2: Publish
  const [selectedPlatform, setSelectedPlatform] = useState('linkedin');
  const [platformContent, setPlatformContent] = useState({});
  const [enabledPlatforms, setEnabledPlatforms] = useState({});
  const [loading, setLoading] = useState(false);
  const [isRefining, setIsRefining] = useState(false);
  
  // Scheduling State
  const [showSchedule, setShowSchedule] = useState(false);
  const [scheduledDate, setScheduledDate] = useState('');
  const [scheduledTime, setScheduledTime] = useState('');

  const [availablePlatforms, setAvailablePlatforms] = useState([]);

  useEffect(() => {
    if (isOpen && post) {
      setStep(1); // Reset to step 1
      let pList = ['linkedin', 'facebook', 'instagram', 'twitter'];
      if (post.platforms) {
        const publishedOn = Array.isArray(post.platforms) ? post.platforms : post.platforms.split(',').map(p => p.trim());
        const normalized = publishedOn.map(p => {
          const l = p.toLowerCase();
          if (l.includes('linkedin')) return 'linkedin';
          if (l.includes('facebook')) return 'facebook';
          if (l.includes('instagram')) return 'instagram';
          if (l.includes('twitter') || l.includes('x')) return 'twitter';
          return null;
        }).filter(Boolean);
        
        if (normalized.length > 0) pList = normalized;
      }
      setAvailablePlatforms(pList);
      setSelectedPlatform(pList[0] || 'linkedin');

      const initialEnabled = {};
      pList.forEach(p => { initialEnabled[p] = true; });
      setEnabledPlatforms(initialEnabled);

      let initialContent = {};
      if (typeof post.content === 'string' && post.content.startsWith('{')) {
        try {
          const json = JSON.parse(post.content);
          pList.forEach(p => {
            initialContent[p] = json[p] || json.default || Object.values(json)[0] || "";
          });
        } catch (e) {
          pList.forEach(p => { initialContent[p] = post.content; });
        }
      } else {
        pList.forEach(p => { initialContent[p] = post.content || ""; });
      }
      setPlatformContent(initialContent);

      const now = new Date();
      now.setHours(now.getHours() + 1);
      setScheduledDate(now.toISOString().split('T')[0]);
      setScheduledTime(now.toTimeString().slice(0, 5));
    }
  }, [isOpen, post]);

  const togglePlatform = (p, e) => {
    e.stopPropagation();
    setEnabledPlatforms(prev => ({
      ...prev,
      [p]: !prev[p]
    }));
  };

  const handleRefineTwitter = async () => {
    setIsRefining(true);
    try {
      const resp = await authAxios.post('/refine-for-twitter', { 
        content: platformContent.twitter 
      });
      if (resp.data?.shortened_content) {
        setPlatformContent(prev => ({
          ...prev,
          twitter: resp.data.shortened_content
        }));
        setMessage('Gemini has optimized your post for X! ✨');
      }
    } catch (err) {
      console.error(err);
      setMessage('Failed to refine content ❌');
    } finally {
      setIsRefining(false);
    }
  };

  const handleAction = async (mode = 'publish') => {
    if (mode === 'schedule' && (!scheduledDate || !scheduledTime)) {
      setMessage('Please select both date and time 🚩');
      return;
    }

    const activePlatformKeys = availablePlatforms.filter(p => enabledPlatforms[p]);
    if (activePlatformKeys.length === 0) {
      setMessage('Please enable at least one platform ⚠️');
      return;
    }

    setLoading(true);
    try {
      const connResp = await authAxios.get('/connections');
      const userConnections = connResp.data || {};
      
      const publishTargets = {};
      activePlatformKeys.forEach(p => {
        const accounts = userConnections[p] || [];
        if (accounts.length > 0) {
          publishTargets[p] = accounts.map(a => a.account_id || a.id);
        }
      });

      if (Object.keys(publishTargets).length === 0) {
        setMessage('No connected accounts found for enabled platforms! ⚠️');
        setLoading(false);
        return;
      }

      const payload = {
        content: JSON.stringify(platformContent),
        image_url: post.image_url,
        media_type: post.media_type || 'image',
        targets: publishTargets
      };

      if (mode === 'publish') {
        await authAxios.post('/post', payload);
        setMessage('Reposted successfully! ✅');
        if (fetchPublished) fetchPublished(true);
      } else {
        const fullDateStr = `${scheduledDate}T${scheduledTime}:00`;
        await authAxios.post('/schedule', {
          ...payload,
          scheduled_for: fullDateStr,
          timezone: user?.timezone || 'UTC'
        });
        setMessage('Scheduled successfully! 📅');
        if (fetchScheduled) fetchScheduled(true);
      }
      onClose();
    } catch (err) {
      console.error(err);
      setMessage(`Failed to ${mode} ❌`);
    } finally {
      setLoading(false);
    }
  };

  const getPlatformIcon = (p) => <PlatformLogo platform={p} className="w-5 h-5" />;

  if (!isOpen) return null;

  const totalEnabled = Object.values(enabledPlatforms).filter(Boolean).length;

  return (
    <AnimatePresence>
      <div className="fixed inset-0 z-[110] flex items-center justify-center p-4">
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
          className="absolute inset-0 bg-slate-900/40 backdrop-blur-md"
        />

        <motion.div
          initial={{ opacity: 0, scale: 0.95, y: 30 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95, y: 30 }}
          className="relative bg-white w-full max-w-[780px] h-[640px] md:h-[580px] max-h-[90vh] rounded-[28px] md:rounded-[40px] shadow-2xl flex flex-col overflow-hidden border border-slate-100"
        >
          {/* Global Header / Progress */}
          <div className="flex items-center justify-between p-6 border-b border-slate-50">
            <div className="flex items-center gap-5">
              <div>
                 <h3 className="text-xl font-black text-[#2B2926] tracking-tight">Repost Assistant</h3>
                 <p className="text-[9px] uppercase font-bold tracking-[0.2em] text-[#F55600]">Step {step}: {step === 1 ? 'Review' : 'Publish'}</p>
              </div>
              
              <div className="flex items-center gap-2">
                <div className={`w-8 h-1 rounded-full transition-all duration-500 ${step === 1 ? 'bg-[#F55600]' : 'bg-emerald-500'}`} />
                <div className={`w-8 h-1 rounded-full transition-all duration-500 ${step === 2 ? 'bg-[#F55600]' : 'bg-slate-100'}`} />
              </div>
            </div>
            
            <button onClick={onClose} className="p-2.5 bg-slate-50 hover:bg-slate-100 rounded-xl text-slate-400 transition-all">
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Main Body */}
          <div className="flex-1 flex overflow-y-auto custom-scrollbar min-h-0">

            {/* STEP 1: REVIEW CONTENT */}
            {step === 1 && (
              <div className="w-full flex flex-col md:flex-row md:h-full">
                {/* Editor Side */}
                <div className="flex-1 flex flex-col p-4 md:p-6 bg-white md:overflow-hidden min-w-0 min-h-[280px] md:min-h-0">
                   <nav className="flex items-center flex-wrap gap-2 mb-6 bg-slate-50 p-2 rounded-2xl border border-slate-100">
                    {availablePlatforms.map(p => (
                      <button
                        key={p}
                        onClick={() => setSelectedPlatform(p)}
                        className={`group py-2 px-3.5 rounded-xl flex items-center justify-between gap-3 transition-all relative border-2 ${
                          selectedPlatform === p 
                          ? 'bg-white border-[#F55600] shadow-md text-[#2B2926] ring-2 ring-orange-50' 
                          : 'bg-slate-50 border-slate-200 text-slate-400 hover:bg-white hover:border-slate-300'
                        }`}
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <span className={`transition-colors shrink-0 ${selectedPlatform === p ? 'text-[#F55600]' : 'text-slate-300'}`}>
                            {getPlatformIcon(p)}
                          </span>
                          <span className="capitalize text-[11px] font-black">{p === 'twitter' ? 'X' : p}</span>
                        </div>
                        <div 
                          onClick={(e) => togglePlatform(p, e)}
                          className={`shrink-0 w-5 h-5 rounded-md flex items-center justify-center border-2 transition-all ${
                            enabledPlatforms[p] 
                            ? 'bg-emerald-500 border-emerald-500 text-white shadow-sm' 
                            : 'bg-white border-slate-200 text-transparent'
                          }`}
                        >
                          <CheckCircle2 className="w-3.5 h-3.5" />
                        </div>
                      </button>
                    ))}
                  </nav>

                  <div className="flex-1 flex flex-col min-h-[200px] md:min-h-0 relative">
                    {!enabledPlatforms[selectedPlatform] && (
                       <div className="absolute inset-0 z-10 bg-slate-50/60 backdrop-blur-[2px] rounded-[32px] flex items-center justify-center flex-col gap-4 text-center">
                         <p className="text-sm font-black text-[#2B2926]">Platform Excluded</p>
                         <button onClick={(e) => togglePlatform(selectedPlatform, e)} className="py-2.5 px-6 bg-slate-900 text-white text-[11px] font-black rounded-xl uppercase tracking-widest">Include Now</button>
                       </div>
                    )}
                    
                    <div className="flex items-center justify-between mb-4 px-2">
                       <span className="text-[11px] font-black text-slate-400 uppercase tracking-widest">Content Editor</span>
                       {selectedPlatform === 'twitter' && enabledPlatforms.twitter && (
                        <button onClick={handleRefineTwitter} disabled={isRefining} className="flex items-center gap-2 px-3 py-1.5 bg-orange-50 text-[#F55600] rounded-lg border border-orange-100/50 hover:bg-orange-100 transition-all">
                           {isRefining ? <Loader2 className="w-3 h-3 animate-spin" /> : <Sparkles className="w-3 h-3" />}
                           <span className="text-[10px] font-black uppercase">Magic Shorten</span>
                        </button>
                       )}
                    </div>
                    
                    <textarea
                      value={platformContent[selectedPlatform] || ''}
                      disabled={!enabledPlatforms[selectedPlatform]}
                      onChange={(e) => setPlatformContent({...platformContent, [selectedPlatform]: e.target.value})}
                      rows={6}
                      className="w-full flex-1 min-h-[180px] md:min-h-0 md:h-full p-4 md:p-8 text-[#2B2926] bg-slate-50/30 border border-slate-100 rounded-[24px] md:rounded-[32px] outline-none focus:bg-white focus:border-orange-200 transition-all resize-none text-[15px] md:text-[16px] font-medium leading-relaxed"
                      placeholder={`What's the plan for ${selectedPlatform}?`}
                    />
                  </div>
                </div>

                {/* Preview Side */}
                <div className="w-full md:w-[240px] bg-slate-50 md:bg-slate-50/10 border-t-4 md:border-t-0 md:border-l border-slate-100 px-4 pt-5 pb-4 md:p-6 flex flex-col gap-3 md:gap-0">
                  <span className="text-[10px] font-black text-slate-500 uppercase tracking-widest md:mb-6">Media Preview</span>

                  <div className="bg-white rounded-[24px] p-2 shadow-sm border border-slate-100 mb-auto aspect-video md:aspect-square max-h-44 md:max-h-none flex items-center justify-center overflow-hidden relative group">
                    {post.image_url ? (
                      <>
                        <img src={post.image_url} alt="Preview" className="w-full h-full object-cover rounded-[18px]" />
                        {post.media_type === 'video' && (
                          <div className="absolute inset-0 flex items-center justify-center bg-[#2B2926]/20"><PlayCircle className="w-12 h-12 text-white" /></div>
                        )}
                      </>
                    ) : (
                      <div className="flex flex-col items-center gap-3 text-slate-300"><ImageIcon className="w-10 h-10" /><span className="text-xs font-black">No Media</span></div>
                    )}
                  </div>

                  <div className="mt-8">
                     {totalEnabled === 0 ? (
                       <p className="text-[11px] text-rose-500 font-black uppercase text-center mb-4 italic">Please select at least one platform</p>
                     ) : (
                        <p className="text-[11px] text-slate-400 font-bold text-center mb-6">Ready to publish to {totalEnabled} platform{totalEnabled !== 1 ? 's' : ''}</p>
                     )}
                     <button
                        disabled={totalEnabled === 0}
                        onClick={() => setStep(2)}
                        className={`w-full py-3.5 rounded-2xl text-sm font-bold uppercase tracking-wide flex items-center justify-center gap-2.5 transition-all ${totalEnabled === 0 ? 'bg-slate-100 text-slate-300 cursor-not-allowed' : 'bg-[#F55600] text-white shadow-lg shadow-orange-200/50 hover:bg-[#ff5a20] hover:shadow-xl active:scale-[0.98]'}`}
                     >
                        <span>Next: Publishing</span>
                        <ArrowRight className="w-4 h-4" />
                     </button>
                  </div>
                </div>
              </div>
            )}

            {/* STEP 2: PUBLISH & SCHEDULE */}
            {step === 2 && (
                <div className="w-full flex-col flex items-center justify-center p-6 bg-white relative">
                  <div className="max-w-[480px] w-full text-center">
                    <div className="w-12 h-12 bg-orange-50 rounded-2xl flex items-center justify-center mx-auto mb-3 border border-orange-100 shadow-sm">
                      <Calendar className="w-6 h-6 text-[#F55600]" />
                    </div>
                    
                    <h4 className="text-xl font-black text-[#2B2926] mb-1 tracking-tight">Finalize Publishing</h4>
                    <p className="text-slate-400 text-[12px] font-medium mb-4">Choose to publish immediately or schedule.</p>

                    <div className="space-y-4">
                      {/* Schedule Toggle */}
                      <div className={`p-4 rounded-[28px] border pb-6 transition-all duration-300 shadow-sm ${showSchedule ? 'bg-white border-orange-200 ring-4 ring-orange-50' : 'bg-slate-50 border-slate-100'}`}>
                        <div className="flex items-center justify-between mb-4">
                          <div className="flex items-center gap-3">
                            <div className={`w-8 h-8 rounded-xl flex items-center justify-center ${showSchedule ? 'bg-[#F55600] text-white' : 'bg-white text-slate-300 border border-slate-100'}`}>
                              <Calendar className="w-4 h-4" />
                            </div>
                            <div className="text-left">
                              <span className="text-[13px] font-black text-[#2B2926] block">Custom Schedule</span>
                              <span className="text-[9px] text-slate-400 font-black uppercase tracking-widest">{showSchedule ? 'Radial Clock UI Enabled' : 'OFF — Posting Now'}</span>
                            </div>
                          </div>
                          <button onClick={() => setShowSchedule(!showSchedule)} className={`w-12 h-6 rounded-full relative transition-colors ${showSchedule ? 'bg-[#F55600]' : 'bg-slate-200'}`}>
                            <div className={`absolute top-1 left-1 w-4 h-4 bg-white rounded-full transition-all shadow-md ${showSchedule ? 'translate-x-6' : ''}`} />
                          </button>
                        </div>

                        {showSchedule && (
                          <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} className="flex flex-col gap-4">
                            <div className="grid grid-cols-2 gap-3">
                              <div className="flex flex-col text-left">
                                <label className="text-[9px] font-black text-slate-400 uppercase tracking-widest ml-1 mb-1">Select Date</label>
                                <input 
                                  type="date"
                                  value={scheduledDate}
                                  onChange={(e) => setScheduledDate(e.target.value)}
                                  className="w-full p-3 bg-white border border-slate-100 rounded-xl text-[12px] font-black outline-none focus:border-orange-400 transition-all shadow-sm"
                                />
                              </div>
                              <div className="flex flex-col text-left">
                                <label className="text-[9px] font-black text-slate-400 uppercase tracking-widest ml-1 mb-1">Select Time</label>
                                <RadialClockPicker 
                                  selectedTime={scheduledTime}
                                  onTimeChange={setScheduledTime}
                                  timezone={user?.timezone}
                                />
                              </div>
                            </div>
                            <p className="text-[9px] font-bold text-slate-400 uppercase tracking-tight">Timezone: <span className="text-[#F55600]">{user?.timezone || 'Asia/Kolkata'}</span></p>
                          </motion.div>
                        )}
                      </div>

                      <div className="flex gap-3 pt-2">
                        <button
                          onClick={() => setStep(1)}
                          className="flex-1 py-3.5 bg-white border border-[#2B2926]/15 text-[#2B2926] font-bold text-[11px] uppercase tracking-widest rounded-2xl hover:bg-slate-50 transition-all flex items-center justify-center gap-2"
                        >
                          <ArrowLeft className="w-4 h-4" />
                          <span>Back</span>
                        </button>
                        <button
                          disabled={loading}
                          onClick={() => handleAction(showSchedule ? 'schedule' : 'publish')}
                          className="flex-[1.5] py-3.5 bg-[#F55600] text-white font-bold text-[11px] uppercase tracking-widest rounded-2xl shadow-lg shadow-orange-200/50 hover:bg-[#ff5a20] hover:shadow-xl transition-all flex items-center justify-center gap-2 active:scale-[0.98]"
                        >
                          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : (
                            <>
                              <Check className="w-4 h-4 stroke-[3px]" />
                              <span>{showSchedule ? 'Schedule' : 'Publish Now'}</span>
                            </>
                          )}
                        </button>
                      </div>
                    </div>
                  </div>
               </div>
            )}

          </div>
        </motion.div>
      </div>
    </AnimatePresence>
  );
};

export default RepostModal;
