'use client'
import React, { useState } from 'react';
import { motion, AnimatePresence, useMotionValue, useTransform } from 'framer-motion';
import { Mail, Lock, Eye, EyeOff, ArrowRight } from 'lucide-react';
import { cn } from "@/lib/utils"

function Input({ className, type, ...props }) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "file:text-foreground placeholder:text-muted-foreground selection:bg-primary selection:text-primary-foreground dark:bg-input/30 border-input flex h-9 w-full min-w-0 rounded-md border bg-transparent px-3 py-1 text-base shadow-xs transition-[color,box-shadow] outline-none file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
        "focus-visible:outline-none",
        "aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive",
        className
      )}
      {...props}
    />
  )
}

export function SignInCard({ onSignIn, onSignUp, email, setEmail, password, setPassword, isLoading, showPassword, setShowPassword }) {
  const [mode, setMode] = useState('signin'); // 'signin' or 'signup'
  const [confirmPassword, setConfirmPassword] = useState('');
  const [focusedInput, setFocusedInput] = useState(null);
  const [rememberMe, setRememberMe] = useState(false);

  // ... (mouseX/mouseY hooks remain same, keeping logic)
  const mouseX = useMotionValue(0);
  const mouseY = useMotionValue(0);
  const rotateX = useTransform(mouseY, [-300, 300], [10, -10]);
  const rotateY = useTransform(mouseX, [-300, 300], [-10, 10]);

  const handleMouseMove = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    mouseX.set(e.clientX - rect.left - rect.width / 2);
    mouseY.set(e.clientY - rect.top - rect.height / 2);
  };

  const handleMouseLeave = () => {
    mouseX.set(0);
    mouseY.set(0);
  };

  const handleSubmit = (event) => {
    event.preventDefault();
    if (mode === 'signin' && onSignIn) {
      onSignIn(event);
    } else if (mode === 'signup' && onSignUp) {
      onSignUp(event);
    }
  };

  return (
    <div className="min-h-screen w-full bg-white relative overflow-hidden flex items-center justify-center p-4">
      {/* Subtle background decoration */}
      <div className="absolute top-0 left-0 w-full h-1 bg-[#F55600]" />
      <div className="absolute top-1/4 -left-20 w-80 h-80 bg-orange-50 rounded-full blur-[120px] opacity-60" />
      <div className="absolute bottom-1/4 -right-20 w-80 h-80 bg-orange-50 rounded-full blur-[120px] opacity-60" />

      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.5 }}
        className="w-full max-w-md relative z-10"
      >
        <div className="relative group">
          {/* Magic Border Animation */}
          <div className="absolute -inset-[2px] rounded-[34px] overflow-hidden">
            <motion.div 
              className="absolute top-0 left-0 h-[2px] w-[50%] bg-gradient-to-r from-transparent via-[#F55600] to-transparent opacity-80"
              animate={{ left: ["-50%", "100%"] }}
              transition={{ duration: 3, ease: "linear", repeat: Infinity }}
            />
            <motion.div 
              className="absolute top-0 right-0 h-[50%] w-[2px] bg-gradient-to-b from-transparent via-[#F55600] to-transparent opacity-80"
              animate={{ top: ["-50%", "100%"] }}
              transition={{ duration: 3, ease: "linear", repeat: Infinity, delay: 0.75 }}
            />
            <motion.div 
              className="absolute bottom-0 right-0 h-[2px] w-[50%] bg-gradient-to-l from-transparent via-[#F55600] to-transparent opacity-80"
              animate={{ right: ["-50%", "100%"] }}
              transition={{ duration: 3, ease: "linear", repeat: Infinity, delay: 1.5 }}
            />
            <motion.div 
              className="absolute bottom-0 left-0 h-[50%] w-[2px] bg-gradient-to-t from-transparent via-[#F55600] to-transparent opacity-80"
              animate={{ bottom: ["-50%", "100%"] }}
              transition={{ duration: 3, ease: "linear", repeat: Infinity, delay: 2.25 }}
            />
          </div>

          <div className="bg-white rounded-[32px] p-10 border border-slate-100 shadow-[0_20px_50px_rgba(0,0,0,0.04)] relative z-10 transition-all duration-500 group-hover:shadow-orange-100/50">
            <div className="text-center space-y-3 mb-10">
              <a href="https://pipelyt.ai/" className="block">
                <motion.div
                  initial={{ y: -20, opacity: 0 }}
                  animate={{ y: 0, opacity: 1 }}
                  transition={{ delay: 0.2 }}
                  className="mx-auto w-14 h-14 rounded-2xl flex items-center justify-center mb-6 cursor-pointer"
                >
                  <img src="/montanary-elevators.png" alt="Montanary Elevators Logo" className="w-full h-full object-contain" />
                </motion.div>
              </a>

              <h1 className="text-3xl font-black text-[#2B2926] tracking-tight">
                {mode === 'signin' ? 'Welcome Back' : 'Create Account'}
              </h1>
              <p className="text-slate-400 text-[10px] font-black uppercase tracking-[0.2em]">
                {mode === 'signin' ? 'Sign in to your AI workspace' : 'Join the precision content era'}
              </p>
            </div>

            <form onSubmit={(e) => {
              e.preventDefault();
              if (mode === 'signup' && password !== confirmPassword) {
                alert('Passwords do not match!');
                return;
              }
              handleSubmit(e);
            }} className="space-y-5">
              <div className="space-y-4">
                <div className="relative group/input">
                  <Mail className={cn("absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors duration-300", 
                    focusedInput === "email" ? 'text-[#F55600]' : 'text-slate-300')} />
                  <Input
                    type="email"
                    placeholder="Email Address"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    onFocus={() => setFocusedInput("email")}
                    onBlur={() => setFocusedInput(null)}
                    required
                    className="w-full bg-slate-50 border-slate-100 focus:border-[#F55600] text-[#2B2926] placeholder:text-slate-400 h-14 pl-12 pr-4 rounded-2xl transition-all duration-300 font-bold"
                  />
                </div>

                <div className="relative group/input">
                  <Lock className={cn("absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors duration-300", 
                    focusedInput === "password" ? 'text-[#F55600]' : 'text-slate-300')} />
                  <Input
                    type={showPassword ? "text" : "password"}
                    placeholder="Password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    onFocus={() => setFocusedInput("password")}
                    onBlur={() => setFocusedInput(null)}
                    required
                    className="w-full bg-slate-50 border-slate-100 focus:border-[#F55600] text-[#2B2926] placeholder:text-slate-400 h-14 pl-12 pr-12 rounded-2xl transition-all duration-300 font-bold"
                  />
                  <button 
                    type="button"
                    onClick={() => setShowPassword(!showPassword)} 
                    className="absolute right-4 top-1/2 -translate-y-1/2 text-slate-300 hover:text-slate-600 transition-colors"
                  >
                    {showPassword ? <Eye className="w-5 h-5" /> : <EyeOff className="w-5 h-5" />}
                  </button>
                </div>

                {mode === 'signup' && (
                  <motion.div 
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    className="relative group/input"
                  >
                    <Lock className={cn("absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors duration-300", 
                      focusedInput === "confirm" ? 'text-[#F55600]' : 'text-slate-300')} />
                    <Input
                      type={showPassword ? "text" : "password"}
                      placeholder="Confirm Password"
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      onFocus={() => setFocusedInput("confirm")}
                      onBlur={() => setFocusedInput(null)}
                      required
                      className="w-full bg-slate-50 border-slate-100 focus:border-[#F55600] text-[#2B2926] placeholder:text-slate-400 h-14 pl-12 pr-4 rounded-2xl transition-all duration-300 font-bold"
                    />
                  </motion.div>
                )}
              </div>

              {mode === 'signin' && (
                <div className="flex items-center justify-between px-1">
                  <label className="flex items-center gap-2 cursor-pointer group/label">
                    <div className="relative flex items-center justify-center">
                      <input
                        type="checkbox"
                        checked={rememberMe}
                        onChange={() => setRememberMe(!rememberMe)}
                        className="appearance-none h-4 w-4 rounded border border-slate-200 bg-slate-50 checked:bg-[#F55600] checked:border-[#F55600] transition-all cursor-pointer"
                      />
                      {rememberMe && (
                        <motion.div initial={{ scale: 0 }} animate={{ scale: 1 }} className="absolute text-white pointer-events-none">
                          <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
                        </motion.div>
                      )}
                    </div>
                    <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest group-hover/label:text-slate-600 transition-colors">Remember me</span>
                  </label>
                  <a href="#" className="text-[10px] font-black text-slate-400 hover:text-[#F55600] uppercase tracking-widest transition-colors">Forgot password?</a>
                </div>
              )}

              <motion.button
                whileHover={{ scale: 1.01, translateY: -2 }}
                whileTap={{ scale: 0.99 }}
                className="w-full bg-[#F55600] text-white font-black text-sm uppercase tracking-[0.2em] h-14 rounded-2xl shadow-xl shadow-orange-100 flex items-center justify-center gap-3 mt-4 hover:bg-slate-900 transition-all duration-500"
              >
                {isLoading ? (
                  <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                ) : (
                  <>
                    {mode === 'signin' ? 'SIGNIN' : 'SIGNUP'}
                    <ArrowRight className="w-5 h-5" />
                  </>
                )}
              </motion.button>
              
              <div className="text-center pt-4">
                <button
                  type="button"
                  onClick={() => setMode(mode === 'signin' ? 'signup' : 'signin')}
                  className="text-[10px] font-black text-slate-400 uppercase tracking-[0.2em] transition-colors"
                >
                  {mode === 'signin' ? (
                    <>Don't have an account? <span className="text-[#F55600]">Sign Up</span></>
                  ) : (
                    <>Already have an account? <span className="text-[#F55600]">Sign In</span></>
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      </motion.div>
    </div>
  );
}
