import React, { useState, useEffect } from 'react';
import { motion, useMotionValue, useTransform } from 'framer-motion';
import { Mail, Lock, Eye, EyeOff, ArrowRight, ChevronLeft } from 'lucide-react';
import { Link } from 'react-router-dom';
import { cn } from "../../lib/utils";
import { LANDING_HOME_HREF } from "../../utils/config";

function Input({ className, type, ...props }) {
  return (
    <input
      type={type}
      className={cn(
        "file:text-foreground placeholder:text-muted-foreground selection:bg-primary selection:text-primary-foreground dark:bg-input/30 border-input flex h-9 w-full min-w-0 rounded-md border bg-transparent px-3 py-1 text-base shadow-xs transition-[color,box-shadow] outline-none file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
        "focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]",
        "aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive",
        className
      )}
      {...props}
    />
  );
}

const SignUpCard = ({ 
  onSignUp, 
  onSwitchToSignIn, 
  email, 
  setEmail, 
  password, 
  setPassword, 
  isLoading, 
  showPassword, 
  setShowPassword,
  signUpStep,
  setSignUpStep,
  otpCode,
  setOtpCode,
  onRequestOTP,
  onVerifyOTP
}) => {
  const [confirmPassword, setConfirmPassword] = useState('');
  const [focusedInput, setFocusedInput] = useState(null);
  // Inline mismatch error shown below Confirm Password field (replaces the
  // browser-native alert() which was hostile UX per TC-SIGNUP-012).
  const [passwordMismatch, setPasswordMismatch] = useState(false);

  // Resend-OTP cooldown — seconds remaining before the user can re-request a
  // code. Guards against email-spam from rapid clicks; 30s is standard across
  // SaaS auth flows. Ticks down via the effect below.
  const [resendCooldown, setResendCooldown] = useState(0);

  useEffect(() => {
    if (resendCooldown <= 0) return;
    const t = setTimeout(() => setResendCooldown(resendCooldown - 1), 1000);
    return () => clearTimeout(t);
  }, [resendCooldown]);

  // Start the cooldown when the card transitions into the OTP step (signUpStep
  // becomes 1), so the initial send is counted and the Resend button is
  // disabled immediately after the first OTP arrives.
  useEffect(() => {
    if (signUpStep === 1) setResendCooldown(30);
  }, [signUpStep]);

  const handleResendOtp = (e) => {
    if (e) e.preventDefault();
    if (resendCooldown > 0) return;
    setResendCooldown(30);
    onRequestOTP && onRequestOTP(e);
  };

  // 3D Tilt Logic
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

  const handleSubmit = (e) => {
    e.preventDefault();
    if (password !== confirmPassword) {
      setPasswordMismatch(true);
      return;
    }
    setPasswordMismatch(false);
    onSignUp(e);
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
        <div className="relative group" onMouseMove={handleMouseMove} onMouseLeave={handleMouseLeave}>
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

          <motion.div
            style={{ rotateX, rotateY, transformStyle: "preserve-3d" }}
            className="bg-white rounded-[32px] p-10 border border-slate-100 shadow-[0_20px_50px_rgba(0,0,0,0.04)] relative z-10 transition-all duration-500 group-hover:shadow-orange-100/50"
          >
            {/* Back-to-landing corner button (same pattern as SignInCard). */}
            <a
              href={LANDING_HOME_HREF}
              title="Back to home"
              aria-label="Back to home"
              className="absolute top-4 left-4 w-10 h-10 rounded-full bg-white border border-slate-100 text-slate-500 hover:text-[#F55600] hover:border-[#F55600]/30 hover:shadow-md transition-all flex items-center justify-center z-10"
            >
              <ChevronLeft className="w-5 h-5" />
            </a>

            <div className="text-center space-y-3 mb-10">
              <a href={LANDING_HOME_HREF} className="block">
                <motion.div
                  initial={{ y: -20, opacity: 0 }}
                  animate={{ y: 0, opacity: 1 }}
                  transition={{ delay: 0.2 }}
                  className="mx-auto w-14 h-14 rounded-2xl flex items-center justify-center mb-6 cursor-pointer"
                >
                  <img src="/montanary-elevators.png" alt="Montanary Elevators Logo" className="w-full h-full object-contain" />
                </motion.div>
              </a>

              <h1 className="text-3xl font-black text-[#2B2926] tracking-tight">Create Account</h1>
              <p className="text-slate-400 text-[10px] font-black uppercase tracking-[0.2em]">Join the precision content era</p>
            </div>

            <form onSubmit={signUpStep === 0 ? handleSubmit : onVerifyOTP} className="space-y-5">
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
                    disabled={signUpStep !== 0}
                    className="w-full bg-slate-50 border-slate-100 focus:border-[#F55600] text-[#2B2926] placeholder:text-slate-400 h-14 pl-12 pr-4 rounded-2xl transition-all duration-300 font-bold"
                  />
                </div>

                {signUpStep === 0 && (
                  <motion.div 
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="space-y-4"
                  >
                    <div className="relative group/input">
                      <Lock className={cn("absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors duration-300", 
                        focusedInput === "password" ? 'text-[#F55600]' : 'text-slate-300')} />
                      <Input
                        type={showPassword ? "text" : "password"}
                        placeholder="Choose Password"
                        value={password}
                        onChange={(e) => { setPassword(e.target.value); if (passwordMismatch) setPasswordMismatch(false); }}
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

                    <div className="relative group/input">
                      <Lock className={cn("absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors duration-300",
                        passwordMismatch ? 'text-red-500' : (focusedInput === "confirm" ? 'text-[#F55600]' : 'text-slate-300'))} />
                      <Input
                        type={showPassword ? "text" : "password"}
                        placeholder="Confirm Password"
                        value={confirmPassword}
                        onChange={(e) => { setConfirmPassword(e.target.value); if (passwordMismatch) setPasswordMismatch(false); }}
                        onFocus={() => setFocusedInput("confirm")}
                        onBlur={() => setFocusedInput(null)}
                        required
                        aria-invalid={passwordMismatch}
                        className={cn(
                          "w-full bg-slate-50 focus:border-[#F55600] text-[#2B2926] placeholder:text-slate-400 h-14 pl-12 pr-4 rounded-2xl transition-all duration-300 font-bold",
                          passwordMismatch ? "border-red-500 focus:border-red-500" : "border-slate-100"
                        )}
                      />
                      {passwordMismatch && (
                        <p className="mt-2 ml-1 text-xs font-bold text-red-500 flex items-center gap-1">
                          Passwords do not match
                        </p>
                      )}
                    </div>
                  </motion.div>
                )}

                {signUpStep === 1 && (
                  <motion.div 
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="relative group/input"
                  >
                    <Lock className={cn("absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors duration-300", 
                      focusedInput === "otp" ? 'text-[#F55600]' : 'text-slate-300')} />
                    <Input
                      type="text"
                      placeholder="Enter Code"
                      value={otpCode}
                      onChange={(e) => setOtpCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                      onFocus={() => setFocusedInput("otp")}
                      onBlur={() => setFocusedInput(null)}
                      required
                      className="w-full bg-slate-50 border-slate-100 focus:border-[#F55600] text-[#2B2926] placeholder:text-slate-400 h-14 rounded-2xl transition-all duration-300 font-bold tracking-[0.25em] text-center"
                    />
                  </motion.div>
                )}
              </div>

              {signUpStep === 1 && (
                <div className="flex items-center justify-between px-1">
                  <button 
                    type="button"
                    onClick={() => setSignUpStep(0)}
                    className="text-[10px] font-black text-slate-400 hover:text-slate-600 uppercase tracking-widest transition-colors"
                  >
                    Change Email
                  </button>
                  <button
                    type="button"
                    onClick={handleResendOtp}
                    disabled={resendCooldown > 0 || isLoading}
                    className="text-[10px] font-black text-[#F55600] hover:text-[#2B2926] uppercase tracking-widest transition-colors disabled:text-slate-300 disabled:cursor-not-allowed disabled:hover:text-slate-300"
                  >
                    {resendCooldown > 0 ? `Resend in ${resendCooldown}s` : 'Resend Code'}
                  </button>
                </div>
              )}

              <motion.button
                type="submit"
                whileHover={{ scale: 1.01, translateY: -2 }}
                whileTap={{ scale: 0.99 }}
                className="w-full bg-[#F55600] text-white font-black text-sm uppercase tracking-[0.2em] h-14 rounded-2xl shadow-xl shadow-orange-100 flex items-center justify-center gap-3 mt-4 hover:bg-slate-900 transition-all duration-500"
              >
                {isLoading ? (
                  <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                ) : (
                  <>
                    CREATE ACCOUNT <ArrowRight className="w-5 h-5" />
                  </>
                )}
              </motion.button>
              
              <div className="text-center pt-4">
                <Link
                  to="/"
                  className="text-[10px] font-black text-slate-400 uppercase tracking-[0.2em] transition-colors hover:text-[#F55600]"
                >
                  Already have an account? <span className="text-[#F55600]">Sign In</span>
                </Link>
              </div>
            </form>
          </motion.div>
        </div>
      </motion.div>
    </div>
  );
};

export default SignUpCard;
