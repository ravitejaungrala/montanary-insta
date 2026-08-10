import React, { useState, useEffect } from 'react';
import { motion, useMotionValue, useTransform } from 'framer-motion';
import { Mail, Lock, Eye, EyeOff, ArrowRight, ChevronLeft, KeyRound, ShieldCheck, Moon, Sun, Globe } from 'lucide-react';
import { Link } from 'react-router-dom';
import { cn } from "../../lib/utils";
import { LANDING_HOME_HREF } from "../../utils/config";

function Input({ className, type, ...props }) {
  return (
    <input
      type={type}
      className={cn(
        "file:text-foreground placeholder:text-slate-500 selection:bg-orange-500 selection:text-white flex h-14 w-full min-w-0 rounded-xl border border-slate-700/50 bg-[#0d1726]/80 px-4 py-1 text-base text-white shadow-inner transition-all duration-200 outline-none file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm focus-visible:outline-none focus:border-[#F55600] focus:ring-1 focus:ring-[#F55600]",
        className
      )}
      {...props}
    />
  );
}

const SignInCard = ({
  onSignIn,
  email,
  setEmail,
  password,
  setPassword,
  isLoading,
  showPassword,
  setShowPassword,
  forgotStep,              // 0 = not in forgot flow, 1 = email, 2 = OTP, 3 = new password
  setForgotStep,
  forgotOtpCode,
  setForgotOtpCode,
  forgotNewPassword,
  setForgotNewPassword,
  onRequestForgotOtp,      // (e) => void
  onVerifyForgotOtp,       // (e) => void
  onResetPassword,         // (e) => void
}) => {
  const [focusedInput, setFocusedInput] = useState(null);
  const [confirmForgotPassword, setConfirmForgotPassword] = useState('');
  const isForgotFlow = Number(forgotStep || 0) > 0;
  const [passwordMismatch, setPasswordMismatch] = useState(false);
  const [forgotResendCooldown, setForgotResendCooldown] = useState(0);
  const [rememberMe, setRememberMe] = useState(false);
  const [theme, setTheme] = useState('dark'); // Styled default dark theme

  useEffect(() => {
    if (forgotResendCooldown <= 0) return;
    const t = setTimeout(() => setForgotResendCooldown(forgotResendCooldown - 1), 1000);
    return () => clearTimeout(t);
  }, [forgotResendCooldown]);

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
  const rotateX = useTransform(mouseY, [-300, 300], [6, -6]);
  const rotateY = useTransform(mouseX, [-300, 300], [-6, 6]);

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
    <div className="min-h-screen w-full bg-[#060b13] relative overflow-hidden flex flex-col items-center justify-between p-6 font-sans">
      {/* Premium background mesh & dot grid */}
      <div className="absolute inset-0 bg-[radial-gradient(#ffffff04_1px,transparent_1px)] [background-size:24px_24px] pointer-events-none" />
      <div className="absolute top-[-10%] left-[-10%] w-[60%] h-[60%] rounded-full bg-[radial-gradient(circle_farthest-side,rgba(245,86,0,0.12),transparent)] blur-[100px] pointer-events-none" />
      <div className="absolute bottom-[-10%] right-[-10%] w-[60%] h-[60%] rounded-full bg-[radial-gradient(circle_farthest-side,rgba(59,130,246,0.1),transparent)] blur-[100px] pointer-events-none" />

      {/* HEADER BAR */}
      <header className="w-full max-w-7xl flex items-center justify-between z-20 relative">
        {/* Brand logo & subtext */}
        <a href={LANDING_HOME_HREF} className="flex items-center gap-3 group">
          <div className="w-10 h-10 rounded-xl bg-slate-900 border border-slate-800 flex items-center justify-center p-1.5 transition-all duration-300 group-hover:border-orange-500/50 shadow-md">
            <img src="/montanary-elevators.png" alt="Montanary Elevators Logo" className="w-full h-full object-contain" />
          </div>
          <div className="flex flex-col">
            <span className="text-white font-bold tracking-wider text-base uppercase leading-tight group-hover:text-orange-500 transition-colors">
              Montanary Elevators
            </span>
            <span className="text-[#F55600] text-[9px] font-black uppercase tracking-[0.18em] leading-none mt-0.5">
              Elevating Spaces. Enriching Lives.
            </span>
          </div>
        </a>

        {/* Theme switcher & Language selection mockups */}
        <div className="flex items-center gap-3">
          <div className="flex items-center bg-[#0d1726]/60 border border-slate-800 rounded-full p-0.5">
            <button 
              type="button" 
              onClick={() => setTheme('light')}
              className={cn("p-1.5 rounded-full transition-all", theme === 'light' ? 'bg-orange-500 text-white' : 'text-slate-500 hover:text-slate-400')}
            >
              <Sun className="w-3.5 h-3.5" />
            </button>
            <button 
              type="button" 
              onClick={() => setTheme('dark')}
              className={cn("p-1.5 rounded-full transition-all", theme === 'dark' ? 'bg-orange-500 text-white' : 'text-slate-500 hover:text-slate-400')}
            >
              <Moon className="w-3.5 h-3.5" />
            </button>
          </div>
          <button type="button" className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-[#0d1726]/60 border border-slate-800 text-slate-300 text-xs font-semibold hover:border-slate-700 transition-all">
            <Globe className="w-3.5 h-3.5 text-slate-500" />
            <span>EN</span>
            <span className="text-[9px] text-slate-500">▼</span>
          </button>
        </div>
      </header>

      {/* LOGIN CARD */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="w-full max-w-md my-auto relative z-10"
      >
        <div className="relative group" onMouseMove={handleMouseMove} onMouseLeave={handleMouseLeave}>
          {/* Subtle Orange/Blue Outline Glow */}
          <div className="absolute -inset-[1px] rounded-3xl bg-gradient-to-r from-orange-500/30 to-blue-500/20 blur-sm group-hover:from-orange-500/50 group-hover:to-blue-500/30 transition-all duration-300" />

          <motion.div
            style={{ rotateX, rotateY, transformStyle: "preserve-3d" }}
            className="bg-[#0b1320]/60 backdrop-blur-xl rounded-[28px] p-8 border border-white/10 shadow-[0_20px_50px_rgba(0,0,0,0.3)] relative z-10 transition-all duration-300"
          >
            {/* Back button for forgot flow */}
            {isForgotFlow && (
              <button
                type="button"
                onClick={() => setForgotStep(0)}
                className="absolute top-5 left-5 w-8 h-8 rounded-lg bg-slate-900/60 border border-slate-800 text-slate-400 hover:text-white hover:border-slate-700 transition-all flex items-center justify-center"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
            )}

            {/* Central App Logo */}
            <div className="text-center space-y-2 mb-8">
              <div className="mx-auto w-12 h-12 rounded-xl bg-slate-950 border border-slate-800 flex items-center justify-center p-2 shadow-inner">
                <img src="/montanary-elevators.png" alt="Montanary Elevators Logo" className="w-full h-full object-contain" />
              </div>

              {!isForgotFlow && (
                <>
                  <h1 className="text-2xl font-bold text-white tracking-tight leading-tight mt-3">Welcome Back</h1>
                  <p className="text-slate-400 text-xs mt-1">Sign in to your Montanary Elevators workspace</p>
                </>
              )}
              {forgotStep === 1 && (
                <>
                  <h1 className="text-2xl font-bold text-white tracking-tight mt-3">Reset Password</h1>
                  <p className="text-slate-400 text-xs mt-1">Step 1 of 3 — Enter your email</p>
                </>
              )}
              {forgotStep === 2 && (
                <>
                  <h1 className="text-2xl font-bold text-white tracking-tight mt-3">Check Your Email</h1>
                  <p className="text-slate-400 text-xs mt-1">Step 2 of 3 — Enter the 6-digit code</p>
                </>
              )}
              {forgotStep === 3 && (
                <>
                  <h1 className="text-2xl font-bold text-white tracking-tight mt-3">New Password</h1>
                  <p className="text-slate-400 text-xs mt-1">Step 3 of 3 — Set a new password</p>
                </>
              )}
            </div>

            {/* ============================ LOGIN FORM ============================ */}
            {!isForgotFlow && (
              <form onSubmit={onSignIn} className="space-y-4">
                <div className="space-y-4 text-left">
                  {/* Email Field */}
                  <div className="space-y-1.5">
                    <label className="text-slate-300 text-xs font-semibold uppercase tracking-wider ml-1">Email Address</label>
                    <div className="relative group/input">
                      <Mail className={cn("absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors duration-300",
                        focusedInput === "email" ? 'text-[#F55600]' : 'text-slate-500')} />
                      <Input
                        type="email"
                        placeholder="Enter your work email"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        onFocus={() => setFocusedInput("email")}
                        onBlur={() => setFocusedInput(null)}
                        required
                        className="pl-12"
                      />
                    </div>
                  </div>

                  {/* Password Field */}
                  <div className="space-y-1.5">
                    <label className="text-slate-300 text-xs font-semibold uppercase tracking-wider ml-1">Password</label>
                    <div className="relative group/input">
                      <Lock className={cn("absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors duration-300",
                        focusedInput === "password" ? 'text-[#F55600]' : 'text-slate-500')} />
                      <Input
                        type={showPassword ? "text" : "password"}
                        placeholder="Enter your password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        onFocus={() => setFocusedInput("password")}
                        onBlur={() => setFocusedInput(null)}
                        required
                        className="pl-12 pr-12"
                      />
                      <button
                        type="button"
                        onClick={() => setShowPassword(!showPassword)}
                        className="absolute right-4 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors"
                      >
                        {showPassword ? <Eye className="w-4 h-4" /> : <EyeOff className="w-4 h-4" />}
                      </button>
                    </div>
                  </div>
                </div>

                <div className="flex items-center justify-between px-1 pt-1">
                  {/* Remember me */}
                  <label className="flex items-center gap-2 cursor-pointer group/label">
                    <div className="relative flex items-center justify-center">
                      <input
                        type="checkbox"
                        checked={rememberMe}
                        onChange={() => setRememberMe(!rememberMe)}
                        className="appearance-none h-4 w-4 rounded border border-slate-700 bg-[#0d1726] checked:bg-[#F55600] checked:border-[#F55600] transition-all cursor-pointer"
                      />
                      {rememberMe && (
                        <div className="absolute text-white pointer-events-none">
                          <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
                        </div>
                      )}
                    </div>
                    <span className="text-xs text-slate-400 group-hover:text-slate-300 transition-colors">Remember me</span>
                  </label>

                  {/* Forgot Password Link */}
                  {setForgotStep && (
                    <button
                      type="button"
                      onClick={() => setForgotStep(1)}
                      className="text-xs font-semibold text-orange-500 hover:text-orange-400 transition-colors"
                    >
                      Forgot Password?
                    </button>
                  )}
                </div>

                {/* Submit Button */}
                <motion.button
                  type="submit"
                  disabled={isLoading}
                  whileHover={{ scale: 1.01 }}
                  whileTap={{ scale: 0.99 }}
                  className="w-full bg-gradient-to-r from-orange-500 to-orange-600 hover:from-orange-600 hover:to-orange-700 text-white font-semibold text-sm py-4 rounded-xl flex items-center justify-center gap-2 transition-all shadow-[0_4px_14px_0_rgba(245,86,0,0.3)] mt-6 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isLoading ? (
                    <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <>
                      Sign In <ArrowRight className="w-4 h-4" />
                    </>
                  )}
                </motion.button>

                {/* OR Separator */}
                <div className="flex items-center gap-3 my-4 py-2">
                  <div className="h-[1px] bg-slate-800 flex-1" />
                  <span className="text-slate-500 text-xs font-bold uppercase tracking-widest">OR</span>
                  <div className="h-[1px] bg-slate-800 flex-1" />
                </div>

                {/* Enterprise SSO Button */}
                <button
                  type="button"
                  className="w-full bg-transparent border border-slate-700/60 text-white hover:border-slate-600 text-sm py-4 rounded-xl flex items-center justify-center gap-2.5 transition-all font-medium"
                >
                  <ShieldCheck className="w-4 h-4 text-orange-500" />
                  <span>Sign in with Enterprise SSO</span>
                </button>

                {/* Registration Redirect */}
                <div className="text-center pt-4">
                  <Link
                    to="/signup"
                    className="text-xs text-slate-400 hover:text-slate-300 transition-colors"
                  >
                    Don't have an account? <span className="text-orange-500 font-semibold">Sign Up</span>
                  </Link>
                </div>
              </form>
            )}

            {/* ====================== FORGOT PASSWORD — STEP 1 (email) ====================== */}
            {forgotStep === 1 && (
              <form onSubmit={onRequestForgotOtp} className="space-y-5">
                <div className="space-y-1.5 text-left">
                  <label className="text-slate-300 text-xs font-semibold uppercase tracking-wider ml-1">Email Address</label>
                  <div className="relative group/input">
                    <Mail className={cn("absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors duration-300",
                      focusedInput === "email" ? 'text-[#F55600]' : 'text-slate-500')} />
                    <Input
                      type="email"
                      placeholder="Enter your registered email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      onFocus={() => setFocusedInput("email")}
                      onBlur={() => setFocusedInput(null)}
                      required
                      className="pl-12"
                    />
                  </div>
                </div>

                <motion.button
                  type="submit"
                  disabled={isLoading}
                  whileHover={{ scale: 1.01 }}
                  whileTap={{ scale: 0.99 }}
                  className="w-full bg-gradient-to-r from-orange-500 to-orange-600 hover:from-orange-600 hover:to-orange-700 text-white font-semibold text-sm py-4 rounded-xl flex items-center justify-center gap-2 transition-all shadow-[0_4px_14px_0_rgba(245,86,0,0.3)] disabled:opacity-50"
                >
                  {isLoading ? (
                    <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <>
                      Send reset code <ArrowRight className="w-4 h-4" />
                    </>
                  )}
                </motion.button>
              </form>
            )}

            {/* ====================== FORGOT PASSWORD — STEP 2 (OTP) ====================== */}
            {forgotStep === 2 && (
              <form onSubmit={onVerifyForgotOtp} className="space-y-5">
                <div className="space-y-1.5 text-left">
                  <label className="text-slate-300 text-xs font-semibold uppercase tracking-wider ml-1">Verification Code</label>
                  <div className="relative group/input">
                    <KeyRound className={cn("absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors duration-300",
                      focusedInput === "otp" ? 'text-[#F55600]' : 'text-slate-500')} />
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
                      className="pl-12 text-center tracking-[0.4em] font-bold"
                    />
                  </div>
                </div>

                <p className="text-xs text-slate-400 text-center leading-relaxed">
                  Code sent to <span className="text-white font-medium">{email}</span>.<br />
                  Check spam folder if it's not in your inbox.
                </p>

                <motion.button
                  type="submit"
                  disabled={isLoading}
                  whileHover={{ scale: 1.01 }}
                  whileTap={{ scale: 0.99 }}
                  className="w-full bg-gradient-to-r from-orange-500 to-orange-600 hover:from-orange-600 hover:to-orange-700 text-white font-semibold text-sm py-4 rounded-xl flex items-center justify-center gap-2 transition-all shadow-[0_4px_14px_0_rgba(245,86,0,0.3)] disabled:opacity-50"
                >
                  {isLoading ? (
                    <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <>
                      Verify Code <ShieldCheck className="w-4 h-4" />
                    </>
                  )}
                </motion.button>

                <div className="flex items-center justify-between text-xs pt-1">
                  <button
                    type="button"
                    onClick={() => setForgotStep(1)}
                    className="text-slate-500 hover:text-white transition-colors"
                  >
                    ← Different email
                  </button>
                  <button
                    type="button"
                    onClick={handleForgotResend}
                    disabled={forgotResendCooldown > 0 || isLoading}
                    className="text-orange-500 hover:text-orange-400 transition-colors disabled:text-slate-600 disabled:cursor-not-allowed"
                  >
                    {forgotResendCooldown > 0 ? `Resend in ${forgotResendCooldown}s` : 'Resend code'}
                  </button>
                </div>
              </form>
            )}

            {/* ====================== FORGOT PASSWORD — STEP 3 (new password) ====================== */}
            {forgotStep === 3 && (
              <form onSubmit={handleResetSubmit} className="space-y-5">
                <div className="space-y-4 text-left">
                  {/* New Password */}
                  <div className="space-y-1.5">
                    <label className="text-slate-300 text-xs font-semibold uppercase tracking-wider ml-1">New Password</label>
                    <div className="relative group/input">
                      <Lock className={cn("absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors duration-300",
                        focusedInput === "newpw" ? 'text-[#F55600]' : 'text-slate-500')} />
                      <Input
                        type={showPassword ? "text" : "password"}
                        placeholder="Enter your new password"
                        value={forgotNewPassword || ''}
                        onChange={(e) => { setForgotNewPassword(e.target.value); if (passwordMismatch) setPasswordMismatch(false); }}
                        onFocus={() => setFocusedInput("newpw")}
                        onBlur={() => setFocusedInput(null)}
                        required
                        minLength={6}
                        className="pl-12 pr-12"
                      />
                      <button
                        type="button"
                        onClick={() => setShowPassword(!showPassword)}
                        className="absolute right-4 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors"
                      >
                        {showPassword ? <Eye className="w-4 h-4" /> : <EyeOff className="w-4 h-4" />}
                      </button>
                    </div>
                  </div>

                  {/* Confirm Password */}
                  <div className="space-y-1.5">
                    <label className="text-slate-300 text-xs font-semibold uppercase tracking-wider ml-1">Confirm Password</label>
                    <div className="relative group/input">
                      <Lock className={cn("absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors duration-300",
                        passwordMismatch ? 'text-red-500' : (focusedInput === "confirmpw" ? 'text-[#F55600]' : 'text-slate-500'))} />
                      <Input
                        type={showPassword ? "text" : "password"}
                        placeholder="Confirm your new password"
                        value={confirmForgotPassword}
                        onChange={(e) => { setConfirmForgotPassword(e.target.value); if (passwordMismatch) setPasswordMismatch(false); }}
                        onFocus={() => setFocusedInput("confirmpw")}
                        onBlur={() => setFocusedInput(null)}
                        required
                        minLength={6}
                        className={cn("pl-12", passwordMismatch && "border-red-500 focus:border-red-500 focus:ring-red-500")}
                      />
                    </div>
                    {passwordMismatch && (
                      <p className="mt-1.5 ml-1 text-xs font-bold text-red-500">
                        Passwords do not match
                      </p>
                    )}
                  </div>
                </div>

                <motion.button
                  type="submit"
                  disabled={isLoading}
                  whileHover={{ scale: 1.01 }}
                  whileTap={{ scale: 0.99 }}
                  className="w-full bg-gradient-to-r from-orange-500 to-orange-600 hover:from-orange-600 hover:to-orange-700 text-white font-semibold text-sm py-4 rounded-xl flex items-center justify-center gap-2 transition-all shadow-[0_4px_14px_0_rgba(245,86,0,0.3)] disabled:opacity-50"
                >
                  {isLoading ? (
                    <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <>
                      Reset password <ArrowRight className="w-4 h-4" />
                    </>
                  )}
                </motion.button>
              </form>
            )}
          </motion.div>
        </div>
      </motion.div>

      {/* FOOTER */}
      <footer className="w-full max-w-7xl flex flex-col sm:flex-row items-center justify-between gap-4 z-20 relative text-xs text-slate-500 mt-6 border-t border-slate-900 pt-6">
        <span>Need help? <a href="#" className="text-orange-500 hover:underline">Contact Support</a></span>
        <div className="flex items-center gap-4">
          <a href="#" className="hover:text-slate-400 transition-colors">Privacy Policy</a>
          <a href="#" className="hover:text-slate-400 transition-colors">Terms of Service</a>
          <a href="#" className="hover:text-slate-400 transition-colors">Security</a>
        </div>
      </footer>
    </div>
  );
};

export default SignInCard;
