import React, { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { Clock, X, ChevronRight, Check, Sparkles } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

const RadialClockPicker = ({ 
  selectedTime, 
  onTimeChange,
  timezone = "UTC"
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [mode, setMode] = useState('hours'); 
  const [time, setTime] = useState(selectedTime || "12:00");

  const [hours, minutes] = time.split(':').map(Number);
  const displayHours = hours === 0 ? 12 : hours > 12 ? hours - 12 : hours;
  const amPm = hours >= 12 ? 'PM' : 'AM';

  const handleHourSelect = (h) => {
    let newH = h;
    if (amPm === 'PM' && h < 12) newH = h + 12;
    if (amPm === 'AM' && h === 12) newH = 0;
    
    const newTime = `${String(newH).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
    setTime(newTime);
    onTimeChange(newTime);
    setMode('minutes');
  };

  const handleMinuteSelect = (m) => {
    const newTime = `${String(hours).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
    setTime(newTime);
    onTimeChange(newTime);
  };

  const toggleAmPm = () => {
    let newH = hours;
    if (amPm === 'AM') {
      newH = hours + 12;
    } else {
      newH = hours - 12;
    }
    const safeH = Math.max(0, Math.min(23, newH));
    const newTime = `${String(safeH).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
    setTime(newTime);
    onTimeChange(newTime);
  };

  const numbers = mode === 'hours' 
    ? [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11] 
    : [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55];

  const getRotation = (val) => {
    if (mode === 'hours') return (val % 12) * 30;
    return val * 6;
  };

  return (
    <div className="relative w-full">
      {/* Trigger Button */}
      <div
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setIsOpen(true);
        }}
        className="w-full px-3 py-2 bg-white border-2 border-[#F55600]/30 rounded-xl flex items-center justify-between cursor-pointer hover:border-[#F55600] hover:bg-[#F55600]/[0.03] transition-all shadow-sm group active:scale-[0.98]"
      >
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded-lg bg-orange-100 flex items-center justify-center group-hover:bg-[#F55600] transition-all">
             <Clock className="w-3.5 h-3.5 text-[#F55600] group-hover:text-white transition-all" />
          </div>
          <span className="text-xs font-black text-[#2B2926] tracking-tight">
            {displayHours}:{String(minutes).padStart(2, '0')} {amPm}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <ChevronRight className="w-4 h-4 text-slate-300 group-hover:translate-x-0.5 transition-transform" />
        </div>
      </div>

      {createPortal(
        <AnimatePresence>
          {isOpen && (
            <div className="fixed inset-0 z-[99999] flex items-center justify-center p-4 overflow-hidden touch-none" key="clock-portal">
              <motion.div 
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onClick={() => setIsOpen(false)}
                className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm"
              />
              
              <motion.div
                initial={{ opacity: 0, scale: 0.85, y: 30 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.85, y: 30 }}
                className="relative bg-white border border-white/20 rounded-[40px] shadow-[0_30px_90px_rgba(0,0,0,0.25)] p-6 flex flex-col items-center w-full max-w-[280px]"
              >
                {/* Header Section */}
                <div className="flex flex-col items-center gap-1 mb-4 w-full">
                  <div className="flex items-center justify-between w-full mb-3 px-1">
                    <p className="text-[9px] font-black text-slate-400 uppercase tracking-widest flex items-center gap-1.5">
                      <Sparkles className="w-3 h-3 text-[#F55600]" />
                      Set Time
                    </p>
                    <button onClick={() => setIsOpen(false)} className="p-1.5 hover:bg-slate-100 rounded-full text-slate-300 transition-colors">
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                  
                  <div className="flex items-center gap-3 py-3 px-6 bg-slate-50 rounded-[20px] border border-slate-100 shadow-inner">
                    <button 
                      onClick={() => setMode('hours')}
                      className={`text-2xl font-black transition-all ${mode === 'hours' ? 'text-[#F55600] scale-110' : 'text-slate-200'}`}
                    >
                      {displayHours}
                    </button>
                    <span className="text-2xl font-black text-slate-100">:</span>
                    <button 
                      onClick={() => setMode('minutes')}
                      className={`text-2xl font-black transition-all ${mode === 'minutes' ? 'text-[#F55600] scale-110' : 'text-slate-200'}`}
                    >
                      {String(minutes).padStart(2, '0')}
                    </button>
                    <button 
                      onClick={toggleAmPm}
                      className="ml-1.5 px-2.5 py-1.5 bg-white border border-slate-100 rounded-lg text-[10px] font-black text-[#F55600] uppercase shadow-sm"
                    >
                      {amPm}
                    </button>
                  </div>
                </div>

                {/* Clock Face Rendering */}
                <div className="relative w-48 h-48 bg-slate-50/50 rounded-full border border-slate-100 flex items-center justify-center p-2 mb-6 shadow-inner">
                  <div className="absolute w-2 h-2 bg-[#F55600] rounded-full z-20 border border-white shadow-sm" />
                  
                  <motion.div 
                    className="absolute bottom-1/2 left-1/2 w-1.5 bg-[#F55600] rounded-full origin-bottom"
                    initial={false}
                    animate={{ 
                      rotate: getRotation(mode === 'hours' ? displayHours : minutes),
                      height: mode === 'hours' ? '60px' : '75px'
                    }}
                    transition={{ type: 'spring', stiffness: 200, damping: 25 }}
                  >
                    <div className="absolute -top-2 -left-2 w-5.5 h-5.5 bg-[#F55600] rounded-full shadow-lg border-2 border-white" />
                  </motion.div>

                  {numbers.map((num, i) => {
                    const angle = (i * 30) - 90;
                    const x = 50 + 38 * Math.cos(angle * (Math.PI / 180));
                    const y = 50 + 38 * Math.sin(angle * (Math.PI / 180));
                    
                    const isSelected = mode === 'hours' ? displayHours === num : minutes === num;

                    return (
                      <button
                        key={num}
                        onClick={() => mode === 'hours' ? handleHourSelect(num) : handleMinuteSelect(num)}
                        className={`absolute w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-black transition-all transform -translate-x-1/2 -translate-y-1/2 cursor-pointer z-10 ${
                          isSelected 
                          ? 'text-[#F55600] bg-white shadow-xl scale-125' 
                          : 'text-slate-400 hover:text-slate-600'
                        }`}
                        style={{ left: `${x}%`, top: `${y}%` }}
                      >
                        {num === 0 ? '00' : num}
                      </button>
                    );
                  })}
                </div>

                <div className="w-full flex-col flex gap-3">
                  <button
                    onClick={() => setIsOpen(false)}
                    className="w-full py-4 bg-[#F55600] text-white text-[11px] font-black uppercase tracking-widest rounded-2xl hover:bg-[#e65a2b] transition-all shadow-xl shadow-orange-100 flex items-center justify-center gap-2 active:scale-[0.97]"
                  >
                    <Check className="w-4 h-4 stroke-[3px]" />
                    <span>Confirm Time</span>
                  </button>
                  
                  <button 
                    onClick={() => setMode(mode === 'hours' ? 'minutes' : 'hours')}
                    className="text-[9px] font-black text-slate-300 uppercase tracking-widest hover:text-[#F55600] transition-all"
                  >
                    Switch to {mode === 'hours' ? 'Minutes' : 'Hours'}
                  </button>
                </div>
              </motion.div>
            </div>
          )}
        </AnimatePresence>,
        document.body
      )}
    </div>
  );
};

export default RadialClockPicker;
