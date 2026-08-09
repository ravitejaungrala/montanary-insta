import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Zap, X } from 'lucide-react';

const AlertModal = ({ isOpen, onClose, title = "Pipelyt", message, actionText, onAction, icon: Icon = Zap }) => {
  return (
    <AnimatePresence>
      {isOpen && (
        <div className="fixed inset-0 z-[230] flex items-center justify-center p-4">
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="absolute inset-0 bg-transparent"
          />

          {/* Modal Container */}
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            className="relative bg-white w-full max-w-[400px] rounded-[32px] overflow-hidden shadow-2xl"
          >
            {/* Header / Accent */}
            <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-orange-400 to-orange-600" />
            
            <div className="p-8 text-center">
              {/* Icon */}

              {/* Text */}
              <h3 className="text-2xl font-black text-[#2B2926] mb-2 tracking-tight">
                {title}
              </h3>
              <p className="text-slate-600 font-medium leading-relaxed mb-8 px-2">
                {message}
              </p>

              {/* Actions */}
              <div className="flex items-center gap-4">
                <button
                  onClick={onClose}
                  className="flex-1 py-3.5 px-6 bg-slate-100 text-slate-600 font-bold rounded-2xl hover:bg-slate-200 transition-all active:scale-95 text-sm"
                >
                  Cancel
                </button>
                {actionText && (
                  <button
                    onClick={() => {
                      if (onAction) onAction();
                      onClose();
                    }}
                    className="flex-1 py-3.5 px-6 bg-[#F55600] text-white font-black rounded-2xl shadow-xl shadow-orange-100 hover:bg-[#e65a2b] transition-all active:scale-95 text-sm"
                  >
                    {actionText}
                  </button>
                )}
              </div>
            </div>

            {/* Close Button x */}
            <button
              onClick={onClose}
              className="absolute top-6 right-6 p-2 text-slate-300 hover:text-slate-500 hover:bg-slate-50 rounded-xl transition-all"
            >
              <X className="w-5 h-5" />
            </button>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
};

export default AlertModal;
