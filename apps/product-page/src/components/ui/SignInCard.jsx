import React, { useState, useEffect } from 'react';
import { motion, useMotionValue, useTransform } from 'framer-motion';
import { Mail, Lock, Eye, EyeOff, ArrowRight, ChevronLeft, KeyRound, ShieldCheck } from 'lucide-react';
import { Link } from 'react-router-dom';
import { cn } from "../../lib/utils";
import { LANDING_HOME_HREF } from "../../utils/config";

function Input({ className, type, ...props }) {
  return (
    <input
      type={type}
      className={cn(
        "file:text-foreground placeholder:text-muted-foreground selection:bg-primary selection:text-primary-foreground dark:bg-input/30 border-input flex h-9 w-full min-w-0 rounded-md border bg-transparent px-3 py-1 text-base shadow-xs transition-[color,box-shadow] outline-none file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
        "focus-visible:outline-none",
        "aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive",
        className
      )}
      {...props}
    />
  );
}

const SignInCard = ({
  onSignIn,
  onSwitchToSignUp,
  email,
  setEmail,
  password,
  setPassword,
  isLoading,
  showPassword,
  setShowPassword,
  // Forgot-password integration (all optional — if any is missing the link hides).
  forgotStep,              // 0 = not in forgot flow, 1 = email, 2 = OTP, 3 = new password
  setForgotStep,
  forgotOtpCode,
  setForgotOtpCode,
  forgotNewPassword,
  setForgotNewPassword,
  onRequestForgotOtp,      // (e) => void  — POST /auth/request-otp purpose='reset_password'
  onVerifyForgotOtp,       // (e) => void  — POST /auth/verify-otp purpose='reset_password'
  onResetPassword,         // (e) => void  — POST /auth/reset-password + auto-login
}) => {
  const [focusedInput, setFocusedInput] = useState(null);
  const [confirmForgotPassword, setConfirmForgotPassword] = useState('');
  const isForgotFlow = Number(forgotStep || 0) > 0;
  // Inline mismatch error shown below the Confirm New Password field on
  // forgot-password step 3 (replaces the browser-native alert() — same
  // pattern as SignUpCard.jsx per TC-SIGNUP-012 fix).
  const [passwordMismatch, setPasswordMismatch] = useState(false);

  // Resend-OTP cooldown for forgot-password step 2 (see SignUpCard for the
  // same pattern on the registration OTP step). 30s window to prevent email
  // spam from rapid clicks.
  const [forgotResendCooldown, setForgotResendCooldown] = useState(0);

  useEffect(() => {
    if (forgotResendCooldown <= 0) return;
    const t = setTimeout(() => setForgotResendCooldown(forgotResendCooldown - 1), 1000);
    return () => clearTimeout(t);
  }, [forgotResendCooldown]);

  // Reset the cooldown whenever we land on step 2 (OTP entry) — the initial
  // request-otp just fired, so the Resend button should start in the disabled
  // cooldown state.
  useEffect(() => {
    if (forgotStep === 2) setForgotResendCooldown(30);
    else setForgotResendCooldown(0);
  }, [forgotStep]);

  const handleForgotResend = (e) => {
    if (e) e.preventDefault();
    if (forgotResendCooldown > 0) return;
    setForgotResendCooldown(30);
    onRequestForgotOtp && onRequestForgotOtp(e);
  };

  const handleResetSubmit = (e) => {
    e.preventDefault();
    if (forgotNewPassword !== confirmForgotPassword) {
      setPasswordMismatch(true);
      return;
    }
    setPasswordMismatch(false);
    onResetPassword && onResetPassword(e);
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
            {/* Back-to-landing corner button. Always routes to the marketing
                site configured in VITE_LANDING_URL (falls back to production). */}
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

              {!isForgotFlow && (
                <>
                  <h1 className="text-[28px] font-semibold text-[#2B2926] tracking-tight leading-tight">Welcome Back</h1>
                  <p className="text-[#67655E] text-[11px] font-semibold uppercase tracking-[0.14em] mt-1.5">Sign in to your AI workspace</p>
                </>
              )}
              {forgotStep === 1 && (
                <>
                  <h1 className="text-[28px] font-semibold text-[#2B2926] tracking-tight leading-tight">Reset Password</h1>
                  <p className="text-[#67655E] text-[11px] font-semibold uppercase tracking-[0.14em] mt-1.5">Step 1 of 3 — Enter your email</p>
                </>
              )}
              {forgotStep === 2 && (
                <>
                  <h1 className="text-[28px] font-semibold text-[#2B2926] tracking-tight leading-tight">Check Your Email</h1>
                  <p className="text-[#67655E] text-[11px] font-semibold uppercase tracking-[0.14em] mt-1.5">Step 2 of 3 — Enter the 6-digit code</p>
                </>
              )}
              {forgotStep === 3 && (
                <>
                  <h1 className="text-[28px] font-semibold text-[#2B2926] tracking-tight leading-tight">New Password</h1>
                  <p className="text-[#67655E] text-[11px] font-semibold uppercase tracking-[0.14em] mt-1.5">Step 3 of 3 — Set a new password</p>
                </>
              )}
            </div>

            {/* ============================ LOGIN MODE ============================ */}
            {!isForgotFlow && (
              <form onSubmit={onSignIn} className="space-y-5">
                <div className="space-y-4">
                  <div className="relative group/input">
                    <Mail className={cn("absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors duration-300",
                      focusedInput === "email" ? 'text-[#F55600]' : 'text-[#67655E]')} />
                    <Input
                      type="email"
                      placeholder="Email Address"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      onFocus={() => setFocusedInput("email")}
                      onBlur={() => setFocusedInput(null)}
                      required
                      className="w-full bg-[#FAF8F4] border border-[#2B2926]/15 focus:border-[#F55600] text-[#2B2926] placeholder:text-[#67655E]/70 h-14 pl-12 pr-4 rounded-2xl transition-colors duration-200 font-medium text-[14px]"
                    />
                  </div>

                  <div className="relative group/input">
                    <Lock className={cn("absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors duration-300",
                      focusedInput === "password" ? 'text-[#F55600]' : 'text-[#67655E]')} />
                    <Input
                      type={showPassword ? "text" : "password"}
                      placeholder="Password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      onFocus={() => setFocusedInput("password")}
                      onBlur={() => setFocusedInput(null)}
                      required
                      className="w-full bg-[#FAF8F4] border border-[#2B2926]/15 focus:border-[#F55600] text-[#2B2926] placeholder:text-[#67655E]/70 h-14 pl-12 pr-12 rounded-2xl transition-colors duration-200 font-medium text-[14px]"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="absolute right-4 top-1/2 -translate-y-1/2 text-[#67655E] hover:text-[#2B2926] transition-colors"
                    >
                      {showPassword ? <Eye className="w-5 h-5" /> : <EyeOff className="w-5 h-5" />}
                    </button>
                  </div>
                </div>

                {setForgotStep && (
                  <div className="flex justify-end">
                    <button
                      type="button"
                      onClick={() => setForgotStep(1)}
                      className="text-[11px] font-semibold text-[#67655E] uppercase tracking-[0.14em] hover:text-[#F55600] transition-colors"
                    >
                      Forgot password?
                    </button>
                  </div>
                )}

                <motion.button
                  type="submit"
                  whileHover={{ scale: 1.01, translateY: -2 }}
                  whileTap={{ scale: 0.99 }}
                  className="w-full bg-[#F55600] text-white font-semibold text-[13px] uppercase tracking-[0.14em] h-14 rounded-2xl shadow-md flex items-center justify-center gap-3 mt-4 hover:bg-[#e63e00] transition-colors duration-200"
                >
                  {isLoading ? (
                    <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <>
                      SIGN IN <ArrowRight className="w-5 h-5" />
                    </>
                  )}
                </motion.button>

                <div className="text-center pt-4">
                  <Link
                    to="/signup"
                    className="text-[11px] font-semibold text-[#67655E] uppercase tracking-[0.14em] transition-colors hover:text-[#F55600]"
                  >
                    Don't have an account? <span className="text-[#F55600]">Sign Up</span>
                  </Link>
                </div>
              </form>
            )}

            {/* ====================== FORGOT PASSWORD — STEP 1 (email) ====================== */}
            {forgotStep === 1 && (
              <form onSubmit={onRequestForgotOtp} className="space-y-5">
                <div className="relative group/input">
                  <Mail className={cn("absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors duration-300",
                    focusedInput === "email" ? 'text-[#F55600]' : 'text-[#67655E]')} />
                  <Input
                    type="email"
                    placeholder="Email Address"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    onFocus={() => setFocusedInput("email")}
                    onBlur={() => setFocusedInput(null)}
                    required
                    className="w-full bg-[#FAF8F4] border border-[#2B2926]/15 focus:border-[#F55600] text-[#2B2926] placeholder:text-[#67655E]/70 h-14 pl-12 pr-4 rounded-2xl transition-colors duration-200 font-medium text-[14px]"
                  />
                </div>

                <motion.button
                  type="submit"
                  whileHover={{ scale: 1.01, translateY: -2 }}
                  whileTap={{ scale: 0.99 }}
                  className="w-full bg-[#F55600] text-white font-semibold text-[13px] uppercase tracking-[0.14em] h-14 rounded-2xl shadow-md flex items-center justify-center gap-3 mt-4 hover:bg-[#e63e00] transition-colors duration-200"
                >
                  {isLoading ? (
                    <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <>
                      Send reset code <ArrowRight className="w-5 h-5" />
                    </>
                  )}
                </motion.button>

                <div className="text-center pt-2">
                  <button
                    type="button"
                    onClick={() => setForgotStep(0)}
                    className="text-[10px] font-black text-slate-400 uppercase tracking-[0.2em] hover:text-[#F55600] transition-colors"
                  >
                    ← Back to sign in
                  </button>
                </div>
              </form>
            )}

            {/* ====================== FORGOT PASSWORD — STEP 2 (OTP) ====================== */}
            {forgotStep === 2 && (
              <form onSubmit={onVerifyForgotOtp} className="space-y-5">
                <div className="relative group/input">
                  <KeyRound className={cn("absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors duration-300",
                    focusedInput === "otp" ? 'text-[#F55600]' : 'text-slate-300')} />
                  <Input
                    type="text"
                    inputMode="numeric"
                    maxLength={6}
                    placeholder="6-digit code"
                    value={forgotOtpCode || ''}
                    onChange={(e) => setForgotOtpCode(e.target.value.replace(/\D/g, ''))}
                    onFocus={() => setFocusedInput("otp")}
                    onBlur={() => setFocusedInput(null)}
                    required
                    className="w-full bg-slate-50 border-slate-100 focus:border-[#F55600] text-[#2B2926] placeholder:text-slate-400 h-14 pl-12 pr-4 rounded-2xl transition-all duration-300 font-bold tracking-[0.4em] text-center"
                  />
                </div>

                <p className="text-[10px] text-slate-500 font-bold text-center">
                  Code sent to <span className="text-[#2B2926]">{email}</span>.
                  Check spam if it's not in your inbox.
                </p>

                <motion.button
                  type="submit"
                  whileHover={{ scale: 1.01, translateY: -2 }}
                  whileTap={{ scale: 0.99 }}
                  className="w-full bg-[#F55600] text-white font-semibold text-[13px] uppercase tracking-[0.14em] h-14 rounded-2xl shadow-md flex items-center justify-center gap-3 mt-4 hover:bg-[#e63e00] transition-colors duration-200"
                >
                  {isLoading ? (
                    <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <>
                      Verify code <ShieldCheck className="w-5 h-5" />
                    </>
                  )}
                </motion.button>

                <div className="flex items-center justify-between pt-2 px-1">
                  <button
                    type="button"
                    onClick={() => setForgotStep(1)}
                    className="text-[10px] font-black text-slate-400 uppercase tracking-[0.2em] hover:text-[#F55600] transition-colors"
                  >
                    ← Use a different email
                  </button>
                  <button
                    type="button"
                    onClick={handleForgotResend}
                    disabled={forgotResendCooldown > 0 || isLoading}
                    className="text-[10px] font-black text-[#F55600] uppercase tracking-[0.15em] hover:text-[#2B2926] transition-colors disabled:text-slate-300 disabled:cursor-not-allowed disabled:hover:text-slate-300"
                  >
                    {forgotResendCooldown > 0 ? `Resend in ${forgotResendCooldown}s` : 'Resend code'}
                  </button>
                </div>
              </form>
            )}

            {/* ====================== FORGOT PASSWORD — STEP 3 (new password) ====================== */}
            {forgotStep === 3 && (
              <form onSubmit={handleResetSubmit} className="space-y-5">
                <div className="relative group/input">
                  <Lock className={cn("absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors duration-300",
                    focusedInput === "newpw" ? 'text-[#F55600]' : 'text-slate-300')} />
                  <Input
                    type={showPassword ? "text" : "password"}
                    placeholder="New password"
                    value={forgotNewPassword || ''}
                    onChange={(e) => { setForgotNewPassword(e.target.value); if (passwordMismatch) setPasswordMismatch(false); }}
                    onFocus={() => setFocusedInput("newpw")}
                    onBlur={() => setFocusedInput(null)}
                    required
                    minLength={6}
                    className="w-full bg-[#FAF8F4] border border-[#2B2926]/15 focus:border-[#F55600] text-[#2B2926] placeholder:text-[#67655E]/70 h-14 pl-12 pr-12 rounded-2xl transition-colors duration-200 font-medium text-[14px]"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-4 top-1/2 -translate-y-1/2 text-[#67655E] hover:text-[#2B2926] transition-colors"
                  >
                    {showPassword ? <Eye className="w-5 h-5" /> : <EyeOff className="w-5 h-5" />}
                  </button>
                </div>

                <div className="relative group/input">
                  <Lock className={cn("absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors duration-300",
                    passwordMismatch ? 'text-red-500' : (focusedInput === "confirmpw" ? 'text-[#F55600]' : 'text-slate-300'))} />
                  <Input
                    type={showPassword ? "text" : "password"}
                    placeholder="Confirm new password"
                    value={confirmForgotPassword}
                    onChange={(e) => { setConfirmForgotPassword(e.target.value); if (passwordMismatch) setPasswordMismatch(false); }}
                    onFocus={() => setFocusedInput("confirmpw")}
                    onBlur={() => setFocusedInput(null)}
                    required
                    minLength={6}
                    aria-invalid={passwordMismatch}
                    className={cn(
                      "w-full bg-[#FAF8F4] focus:border-[#F55600] text-[#2B2926] placeholder:text-[#67655E]/70 h-14 pl-12 pr-4 rounded-2xl transition-colors duration-200 font-medium text-[14px]",
                      passwordMismatch ? "border border-red-500 focus:border-red-500" : "border border-[#2B2926]/15"
                    )}
                  />
                  {passwordMismatch && (
                    <p className="mt-2 ml-1 text-xs font-bold text-red-500">
                      Passwords do not match
                    </p>
                  )}
                </div>

                <motion.button
                  type="submit"
                  whileHover={{ scale: 1.01, translateY: -2 }}
                  whileTap={{ scale: 0.99 }}
                  className="w-full bg-[#F55600] text-white font-semibold text-[13px] uppercase tracking-[0.14em] h-14 rounded-2xl shadow-md flex items-center justify-center gap-3 mt-4 hover:bg-[#e63e00] transition-colors duration-200"
                >
                  {isLoading ? (
                    <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <>
                      Reset password <ArrowRight className="w-5 h-5" />
                    </>
                  )}
                </motion.button>

                <div className="text-center pt-2">
                  <button
                    type="button"
                    onClick={() => { setPasswordMismatch(false); setForgotStep(0); }}
                    className="text-[10px] font-black text-slate-400 uppercase tracking-[0.2em] hover:text-[#F55600] transition-colors"
                  >
                    ← Cancel and return to sign in
                  </button>
                </div>
              </form>
            )}
          </motion.div>
        </div>
      </motion.div>
    </div>
  );
};

export default SignInCard;
