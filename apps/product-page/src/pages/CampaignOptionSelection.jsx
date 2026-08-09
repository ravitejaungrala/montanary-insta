import React from 'react';
import { Zap, PlusSquare, Sparkles, ArrowRight, MousePointer2 } from 'lucide-react';
import { motion } from 'framer-motion';

const OptionCard = ({ icon: Icon, title, description, badge, onClick, gradient, delay }) => (
  <motion.div
    initial={{ opacity: 0, scale: 0.95 }}
    animate={{ opacity: 1, scale: 1 }}
    transition={{ duration: 0.4, delay }}
    onClick={onClick}
    className="group relative cursor-pointer h-full"
  >
    <div className="p-6 h-full bg-white rounded-[24px] border-2 border-[#2B2926] hover:border-[#F55600]/30 transition-all duration-500 shadow-lg shadow-slate-200/40 hover:shadow-2xl hover:shadow-[#F55600]/10 flex flex-col gap-4 overflow-hidden">
      <div className="flex justify-between items-start relative z-10">
        <div className={`w-12 h-12 rounded-xl bg-gradient-to-br ${gradient} flex items-center justify-center text-white shadow-md shadow-[#F55600]/20 group-hover:scale-110 transition-transform duration-500`}>
          <Icon className="w-6 h-6" />
        </div>
        {badge && (
          <div className="flex items-center gap-1.5 text-[#F55600] text-[10px] font-semibold uppercase tracking-[0.2em] py-1">
            <div className="w-1.5 h-1.5 rounded-full bg-[#F55600] animate-pulse shadow-[0_0_8px_#F55600]" />
            {badge}
          </div>
        )}
      </div>

      <div className="space-y-2 relative z-10">
        <h3 className="text-xl font-semibold text-[#2B2926] tracking-tight group-hover:text-[#F55600] transition-colors">
          {title}
        </h3>
        <p className="text-[#2B2926] font-semibold text-xs leading-relaxed">
          {description}
        </p>
      </div>

      {/* Start Creating Link */}
      <div className="mt-auto pt-2 flex items-center gap-2 text-[#F55600] font-semibold text-[10px] uppercase tracking-widest group-hover:gap-3 transition-all duration-300 relative z-10">
        Start Creating
        <ArrowRight className="w-3 h-3" />
      </div>
    </div>
  </motion.div>
);

const CampaignOptionSelection = ({ onSelect }) => {
  return (
    <div className="min-h-[75vh] flex flex-col items-center justify-center p-4 lg:p-8 bg-white">
      <div className="max-w-3xl w-full">
        {/* Header Section */}
        <div className="text-center mb-10 space-y-3">
          <motion.div
            initial={{ opacity: 0, y: -15 }}
            animate={{ opacity: 1, y: 0 }}
            className="inline-flex items-center gap-2 px-3 py-1 bg-[#2B2926] rounded-full border border-[#2B2926] text-white text-[9px] font-semibold uppercase tracking-[0.2em] mb-2 shadow-sm"
          >
            <Sparkles className="w-3 h-3 text-white fill-white" />
            Selection
          </motion.div>
          <motion.h1
            initial={{ opacity: 0, y: 15 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
            className="text-3xl sm:text-4xl font-semibold text-[#2B2926] tracking-tight leading-tight"
          >
            Choose your <span className="text-transparent bg-clip-text bg-gradient-to-r from-[#F55600] to-[#F55600]">Workflow</span>
          </motion.h1>
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.2 }}
            className="text-slate-600 font-semibold text-sm max-w-md mx-auto"
          >
            Select a method to start your next campaign.
          </motion.p>
        </div>

        {/* Options Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-stretch px-4 sm:px-0">
          <OptionCard
            icon={Zap}
            title="AI Campaign"
            description="AI-driven strategic content generation based on your brand identity."
            badge="AI Powered"
            gradient="from-[#F55600] to-[#F55600]"
            delay={0.3}
            onClick={() => onSelect('agent-post')}
          />
          <OptionCard
            icon={PlusSquare}
            title="Manual Post"
            description="Manually craft and schedule custom posts with your own media."
            gradient="from-emerald-500 to-emerald-700"
            delay={0.4}
            onClick={() => onSelect('posts')}
          />
        </div>
      </div>
    </div>
  );
};

export default CampaignOptionSelection;
