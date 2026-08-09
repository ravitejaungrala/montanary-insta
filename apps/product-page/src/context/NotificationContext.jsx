import React, { createContext, useContext, useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Zap, CheckCircle2, AlertCircle, Info, X } from 'lucide-react';

const NotificationContext = createContext();

export const useNotification = () => {
    const context = useContext(NotificationContext);
    if (!context) {
        throw new Error('useNotification must be used within a NotificationProvider');
    }
    return context;
};

export const NotificationProvider = ({ children }) => {
    const [notifications, setNotifications] = useState([]);
    const [confirmConfig, setConfirmConfig] = useState(null);
    const [promptConfig, setPromptConfig] = useState(null);

    const showNotification = useCallback((message, type = 'success') => {
        const id = Math.random().toString(36).substr(2, 9);
        setNotifications((prev) => [...prev, { id, message, type }]);
        setTimeout(() => {
            setNotifications((prev) => prev.filter((n) => n.id !== id));
        }, 4000);
    }, []);

    const confirm = useCallback((config) => {
        return new Promise((resolve) => {
            setConfirmConfig({
                ...config,
                onConfirm: () => {
                    setConfirmConfig(null);
                    resolve(true);
                },
                onCancel: () => {
                    setConfirmConfig(null);
                    resolve(false);
                }
            });
        });
    }, []);

    const prompt = useCallback((config) => {
        return new Promise((resolve) => {
            setPromptConfig({
                ...config,
                onConfirm: (value) => {
                    setPromptConfig(null);
                    resolve(value);
                },
                onCancel: () => {
                    setPromptConfig(null);
                    resolve(null);
                }
            });
        });
    }, []);

    const toast = {
        success: (msg) => showNotification(msg, 'success'),
        error: (msg) => showNotification(msg, 'error'),
        info: (msg) => showNotification(msg, 'info'),
        warning: (msg) => showNotification(msg, 'warning')
    };

    return (
        <NotificationContext.Provider value={{ toast, confirm, prompt }}>
            {children}
            
            {/* Global Toasts */}
            <div className="fixed bottom-8 right-8 z-[1000] flex flex-col gap-3 pointer-events-none">
                <AnimatePresence>
                    {notifications.map((n) => (
                        <motion.div
                            key={n.id}
                            initial={{ opacity: 0, x: 50, scale: 0.9 }}
                            animate={{ opacity: 1, x: 0, scale: 1 }}
                            exit={{ opacity: 0, x: 20, scale: 0.9 }}
                            className="pointer-events-auto flex items-center gap-3 px-6 py-4 bg-white border-2 border-slate-100 rounded-2xl shadow-2xl min-w-[320px] max-w-md relative overflow-hidden"
                            style={{
                                borderLeftColor: n.type === 'success' ? '#22c55e' : n.type === 'error' ? '#ef4444' : n.type === 'warning' ? '#f59e0b' : '#3b82f6',
                                borderLeftWidth: '6px'
                            }}
                        >
                            <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${
                                n.type === 'success' ? 'bg-green-50 text-green-600' :
                                n.type === 'error' ? 'bg-red-50 text-red-600' :
                                n.type === 'warning' ? 'bg-amber-50 text-amber-600' :
                                'bg-blue-50 text-blue-600'
                            }`}>
                                {n.type === 'success' ? <CheckCircle2 className="w-5 h-5" /> :
                                 n.type === 'error' ? <AlertCircle className="w-5 h-5" /> :
                                 n.type === 'warning' ? <AlertCircle className="w-5 h-5" /> :
                                 <Info className="w-5 h-5" />}
                            </div>
                            <div className="flex-1">
                                <p className="text-[10px] font-black uppercase tracking-widest text-slate-400">
                                    {n.type.toUpperCase()}
                                </p>
                                <p className="text-sm font-bold text-[#2B2926] leading-tight">
                                    {n.message}
                                </p>
                            </div>
                            <button 
                                onClick={() => setNotifications(prev => prev.filter(item => item.id !== n.id))}
                                className="p-1 hover:bg-slate-50 rounded-lg text-slate-400 transition-all"
                            >
                                <X className="w-4 h-4" />
                            </button>
                        </motion.div>
                    ))}
                </AnimatePresence>
            </div>

            {/* Global Confirm Modal */}
            <AnimatePresence>
                {confirmConfig && (
                    <div className="fixed inset-0 z-[2000] flex items-center justify-center p-4">
                        <motion.div 
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm"
                            onClick={confirmConfig.onCancel}
                        />
                        <motion.div
                            initial={{ opacity: 0, scale: 0.9, y: 20 }}
                            animate={{ opacity: 1, scale: 1, y: 0 }}
                            exit={{ opacity: 0, scale: 0.9, y: 20 }}
                            className="bg-white rounded-3xl shadow-2xl w-full max-w-md overflow-hidden relative border-2 border-slate-100"
                        >
                            <div className="p-8 text-center text-[#2B2926]">
                                <h3 className="text-2xl font-black text-[#2B2926] mb-2 leading-tight">
                                    {confirmConfig.title || 'Are you sure?'}
                                </h3>
                                <p className="text-slate-500 font-medium leading-relaxed">
                                    {confirmConfig.message}
                                </p>
                            </div>
                            <div className="p-6 bg-slate-50 flex items-center gap-3">
                                <button 
                                    onClick={confirmConfig.onCancel}
                                    className="flex-1 px-6 py-4 rounded-xl font-bold text-slate-600 hover:bg-slate-200 transition-all text-sm outline-none"
                                >
                                    {confirmConfig.cancelText || 'No, Cancel'}
                                </button>
                                <button 
                                    onClick={confirmConfig.onConfirm}
                                    className="flex-1 px-6 py-4 rounded-xl font-bold bg-[#F55600] text-white shadow-lg shadow-orange-200/50 hover:shadow-xl hover:scale-105 transition-all text-sm outline-none"
                                >
                                    {confirmConfig.confirmText || 'Yes, Proceed'}
                                </button>
                            </div>
                        </motion.div>
                    </div>
                )}
            </AnimatePresence>

            {/* Global Prompt Modal */}
            <AnimatePresence>
                {promptConfig && (
                    <div className="fixed inset-0 z-[2000] flex items-center justify-center p-4">
                        <motion.div 
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm"
                            onClick={promptConfig.onCancel}
                        />
                        <motion.div
                            initial={{ opacity: 0, scale: 0.9, y: 20 }}
                            animate={{ opacity: 1, scale: 1, y: 0 }}
                            exit={{ opacity: 0, scale: 0.9, y: 20 }}
                            className="bg-white rounded-3xl shadow-2xl w-full max-w-md overflow-hidden relative border-2 border-slate-100"
                        >
                            <div className="p-8 text-[#2B2926]">
                                <div className="w-16 h-16 bg-blue-50 rounded-2xl flex items-center justify-center mb-6 border-2 border-blue-100">
                                    <Info className="w-8 h-8 text-blue-600" />
                                </div>
                                <h3 className="text-xl font-black text-[#2B2926] mb-2 leading-tight">
                                    {promptConfig.title || 'Input required'}
                                </h3>
                                <p className="text-sm font-medium text-slate-500 leading-relaxed mb-6">
                                    {promptConfig.message}
                                </p>
                                <form 
                                    onSubmit={(e) => {
                                        e.preventDefault();
                                        promptConfig.onConfirm(e.target.userInput.value);
                                    }}
                                >
                                    <input 
                                        autoFocus
                                        name="userInput"
                                        type="text"
                                        placeholder={promptConfig.placeholder || 'Type here...'}
                                        defaultValue={promptConfig.defaultValue || ''}
                                        className="w-full px-4 py-3 bg-slate-50 border-2 border-slate-100 focus:border-[#F55600] focus:bg-white rounded-xl outline-none text-sm font-bold transition-all shadow-inner"
                                    />
                                    <div className="flex items-center gap-3 mt-8">
                                        <button 
                                            type="button"
                                            onClick={promptConfig.onCancel}
                                            className="flex-1 px-6 py-4 rounded-xl font-black text-slate-400 hover:text-slate-600 hover:bg-slate-50 transition-all text-[11px] uppercase tracking-widest"
                                        >
                                            {promptConfig.cancelText || 'Cancel'}
                                        </button>
                                        <button 
                                            type="submit"
                                            className="flex-1 px-6 py-4 rounded-xl font-black bg-[#F55600] text-white shadow-lg shadow-orange-200/50 hover:shadow-xl hover:scale-105 transition-all text-[11px] uppercase tracking-widest"
                                        >
                                            {promptConfig.confirmText || 'Submit'}
                                        </button>
                                    </div>
                                </form>
                            </div>
                        </motion.div>
                    </div>
                )}
            </AnimatePresence>
        </NotificationContext.Provider>
    );
};
