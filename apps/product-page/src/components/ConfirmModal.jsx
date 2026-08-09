import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { AlertCircle, Trash2, X } from 'lucide-react';

const ConfirmModal = ({ 
  isOpen, 
  title = "Are you sure?", 
  message, 
  onConfirm, 
  onCancel, 
  confirmText = "Yes, Delete", 
  cancelText = "Cancel",
  type = "danger" // danger, warning, info
}) => {
  if (!isOpen) return null;

  const isDanger = type === 'danger';

  return (
    <AnimatePresence>
      {isOpen && (
        <div className="fixed inset-0 z-[1000] flex items-center justify-center p-4">
          {/* Transparent click-catcher — was a darkened/blurred backdrop;
              the user asked for the grey-blur to go away so the page
              behind stays fully visible while the dialog is open. */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onCancel}
            className="absolute inset-0"
          />
          <motion.div
            initial={{ scale: 0.95, opacity: 0, y: 20 }}
            animate={{ scale: 1, opacity: 1, y: 0 }}
            exit={{ scale: 0.95, opacity: 0, y: 20 }}
            className="bg-white w-full max-w-sm rounded-3xl shadow-2xl relative z-10 overflow-hidden border-2 border-slate-100"
          >
            <div className="p-6">
              <div className="flex items-center justify-between mb-4">
                <span className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-300">Pipelyt</span>
                <div className={`w-12 h-12 rounded-2xl flex items-center justify-center ${isDanger ? 'bg-red-50 text-[#F55600]' : 'bg-orange-50 text-[#F55600]'}`}>
                  {isDanger ? <Trash2 size={24} /> : <AlertCircle size={24} />}
                </div>
                <div className="w-10" /> {/* spacer */}
              </div>
              
              <div className="text-center">
                <h3 className="text-xl font-black text-[#2B2926] mb-2 tracking-tight">{title}</h3>
                <p className="text-sm font-medium text-slate-500 leading-relaxed">
                  {message}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-3 p-6 pt-0">
              <button
                onClick={onCancel}
                className="flex-1 px-4 py-3 rounded-2xl text-sm font-black uppercase tracking-widest text-slate-500 hover:bg-slate-50 transition-colors"
              >
                {cancelText}
              </button>
              <button
                onClick={onConfirm}
                className={`flex-1 px-4 py-3 rounded-2xl text-sm font-black uppercase tracking-widest text-white shadow-lg transition-all active:scale-95 ${isDanger ? 'bg-[#F55600] hover:bg-[#e63e00] shadow-red-200' : 'bg-[#F55600] hover:bg-[#F55600] shadow-orange-200'}`}
              >
                {confirmText}
              </button>
            </div>
            
            <button 
              onClick={onCancel}
              className="absolute top-4 right-4 p-2 text-slate-400 hover:text-slate-600 rounded-xl hover:bg-slate-50 transition-all"
            >
              <X size={18} />
            </button>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
};

export default ConfirmModal;
