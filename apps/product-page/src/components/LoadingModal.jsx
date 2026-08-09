import React from 'react';
import { CheckCircle2, Zap, Search, PenTool, Layout, X } from 'lucide-react';

const LoadingModal = ({ show, onClose }) => {
  const [progress, setProgress] = React.useState(0);
  const [activeStep, setActiveStep] = React.useState(0);

  React.useEffect(() => {
    if (show) {
      setProgress(0);
      setActiveStep(0);
      const interval = setInterval(() => {
        setProgress(prev => {
          const next = prev + Math.random() * 15;
          if (next >= 100) return 99;
          
          if (next > 75) setActiveStep(3);
          else if (next > 50) setActiveStep(2);
          else if (next > 25) setActiveStep(1);
          
          return next;
        });
      }, 800);
      return () => clearInterval(interval);
    }
  }, [show]);

  if (!show) return null;

  const steps = [
    { label: 'Refining Strategy Agent', icon: Zap },
    { label: 'Deep Researching Company/URL', icon: Search },
    { label: 'Drafting Strategic Variants', icon: PenTool },
    { label: 'Designing Visual Assets', icon: Layout },
  ];

  return (
    <div className="fixed inset-0 flex items-center justify-center z-[100] p-4 animate-in fade-in duration-500">
      <div className="absolute inset-0 bg-[#2B2926]/30 backdrop-blur-md" onClick={onClose} />
      <div className="bg-white p-10 rounded-[32px] shadow-[0_24px_60px_rgba(43,41,38,0.15)] w-[440px] max-w-full text-center border border-[#2B2926]/15 animate-in zoom-in-95 duration-500 scale-100 overflow-hidden relative">
        {/* Close Button */}
        <button 
          onClick={onClose}
          className="absolute top-6 right-6 p-2 hover:bg-[#2B2926]/[0.06] rounded-full transition-all text-[#2B2926] hover:text-[#2B2926] z-20"
          title="Run in background"
        >
          <X className="w-5 h-5" />
        </button>

        <div className="absolute inset-0 bg-gradient-to-tr from-white via-orange-50/20 to-white opacity-40 pointer-events-none"></div>

        <div className="relative z-10">
          <div className="w-20 h-20 bg-orange-50 rounded-3xl mx-auto mb-8 flex items-center justify-center border border-orange-100/50 animate-pulse-soft">
              <Zap className="w-10 h-10 text-[#F55600]" fill="#F55600" />
          </div>
          
          <h3 className="text-2xl font-semibold text-[#2B2926] mb-2 tracking-tight">Orchestrating Strategy</h3>
          <p className="text-[13px] text-[#67655E] mb-8 px-6 font-normal leading-relaxed">
            Our AI agents are analyzing your brief and industry trends to build premium content assets.
          </p>
          
          {/* Professional Progress Bar */}
          <div className="w-full bg-[#2B2926]/[0.06] h-2 rounded-full mb-10 overflow-hidden border border-[#2B2926]/15 p-[1.5px]">
              <div 
                className="h-full bg-gradient-to-r from-[#F55600] to-orange-400 rounded-full transition-all duration-700 ease-out shadow-sm animate-shimmer"
                style={{ width: `${progress}%` }}
              ></div>
          </div>
          
          <div className="space-y-4 text-left px-4">
              {steps.map((step, i) => {
                const Icon = step.icon;
                const isDone = activeStep > i;
                const isActive = activeStep === i;
                
                return (
                  <div key={i} className={`flex items-center gap-4 transition-all duration-500 ${isDone || isActive ? 'opacity-100' : 'opacity-20'}`}>
                      <div className={`w-7 h-7 rounded-lg flex items-center justify-center transition-all duration-500 ${
                        isDone ? 'bg-[#50C878] text-white shadow-lg shadow-green-100' :
                        isActive ? 'bg-[#F55600] text-white shadow-lg shadow-orange-100 rotate-12' :
                        'bg-[#2B2926]/[0.06] text-[#67655E]'
                      }`}>
                          {isDone ? <CheckCircle2 className="w-4 h-4" /> : <Icon className="w-4 h-4" />}
                      </div>
                      <div className="flex flex-col">
                        <span className={`text-[13px] font-semibold tracking-tight ${isActive ? 'text-[#F55600]' : 'text-[#2B2926]'}`}>
                          {step.label}
                        </span>
                        {isActive && <span className="text-[10px] text-[#F55600] font-medium animate-pulse mt-0.5">Processing...</span>}
                      </div>
                  </div>
                );
              })}
          </div>
        </div>
      </div>
    </div>
  );
};

export default LoadingModal;
