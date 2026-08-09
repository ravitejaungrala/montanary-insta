import React, { useState, useEffect } from 'react';
import { CheckCircle2, Users, Loader2 } from 'lucide-react';

const AccountSelectionModal = ({ show, onClose, platform, accounts, onSelect, isConnecting }) => {
  const [selectedAccountIds, setSelectedAccountIds] = useState([]);

  useEffect(() => {
    if (show) {
      setSelectedAccountIds([]);
    }
  }, [show]);

  const formatPlatformName = (p) => {
    if (!p) return '';
    if (p.toLowerCase() === 'linkedin') return 'LinkedIn';
    return p.charAt(0).toUpperCase() + p.slice(1);
  };

  const handleToggleAccount = (accountId) => {
    if (isConnecting) return;
    const sId = String(accountId);
    setSelectedAccountIds(prev => 
      prev.includes(sId) 
        ? prev.filter(id => id !== sId) 
        : [...prev, sId]
    );
  };

  const isSelected = (accountId) => selectedAccountIds.includes(String(accountId));

  // C-7: wait for the async onSelect to finish before closing so the user
  // sees any errors. Previously the modal closed immediately.
  const handleSubmit = async () => {
    if (selectedAccountIds.length === 0 || isConnecting) return;
    try {
      await onSelect(platform, selectedAccountIds);
      onClose();
    } catch (err) {
      console.error('[AccountSelectionModal] Error in onSelect:', err);
    }
  };

  if (!show || !platform) return null;

  // Safety: Ensure accounts is an array before filtering
  const safeAccounts = Array.isArray(accounts) ? accounts : [];
  const personalAccounts = safeAccounts.filter(a => a?.type === 'profile');
  const businessAccounts = safeAccounts.filter(a => a?.type !== 'profile');

  const renderAccountList = (list) => (
    list.map(account => (
      <div 
        key={account.id} 
        className={`flex items-center justify-between p-3 rounded-xl border border-slate-100 transition-all group ${
          isConnecting ? 'opacity-70 cursor-not-allowed' : 'hover:bg-slate-50 cursor-pointer'
        }`}
        onClick={() => handleToggleAccount(account.id)}
      >
        <div className="flex items-center gap-4">
          <div className={`w-6 h-6 rounded flex items-center justify-center border-2 transition-all ${
            isSelected(account.id) 
              ? 'bg-[#3E4C59] border-[#3E4C59]' 
              : 'border-slate-300'
          }`}>
            {isSelected(account.id) && <CheckCircle2 className="w-4 h-4 text-white" />}
          </div>
          <div className="relative">
            {account.profile_picture_url ? (
              <img 
                src={account.profile_picture_url} 
                alt={account.name} 
                className="w-10 h-10 rounded-full object-cover border" 
              />
            ) : (
              <div className="w-10 h-10 rounded-full bg-slate-100 flex items-center justify-center text-slate-400">
                <Users className="w-5 h-5" />
              </div>
            )}
          </div>
          <div>
            <p className="font-bold text-[#2B2926] leading-tight">{account.name}</p>
            <p className="text-xs text-slate-400">{account.type === 'profile' ? 'Personal Profile' : `@${account.name.toLowerCase().replace(/\s/g, '')}`}</p>
          </div>
        </div>
      </div>
    ))
  );

  return (
    <div className="fixed inset-0 flex items-center justify-center z-[9999] p-4">
      <div 
        className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm" 
        onClick={!isConnecting ? onClose : undefined} 
      />
      {/* C-16: max-width-full → max-w-full (valid Tailwind utility) */}
      <div 
        className="bg-white p-8 rounded-[32px] shadow-2xl w-[500px] max-w-full relative z-10"
        onClick={e => e.stopPropagation()}
      >
        <h3 className="text-2xl font-bold text-[#2B2926] mb-1">Connect {formatPlatformName(platform)} Accounts</h3>
        <p className="text-slate-500 mb-8 text-sm">Choose which {platform} accounts you want to connect.</p>
        
        <div className={`space-y-6 max-h-[60vh] overflow-y-auto pr-2 custom-scrollbar ${isConnecting ? 'pointer-events-none' : ''}`}>
          {personalAccounts.length > 0 && (
            <div>
              <h4 className="text-sm font-bold text-[#2B2926] mb-3 ml-1">Personal Profile</h4>
              <div className="space-y-2">
                {renderAccountList(personalAccounts)}
              </div>
            </div>
          )}
          
          {businessAccounts.length > 0 && (
            <div>
              <h4 className="text-sm font-bold text-[#2B2926] mb-3 ml-1 mt-2">Business Pages</h4>
              <div className="space-y-2">
                {renderAccountList(businessAccounts)}
              </div>
            </div>
          )}

          {safeAccounts.length === 0 && (
            <p className="text-slate-500 text-center py-8">No accounts found.</p>
          )}
        </div>

        <div className="mt-8 flex items-center justify-end gap-4">
          <button 
            onClick={onClose} 
            disabled={isConnecting}
            className="px-6 py-3 rounded-xl text-slate-600 font-bold hover:bg-slate-50 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Cancel
          </button>
          {/* C-8: disabled during isConnecting to prevent double-submit */}
          <button
            onClick={handleSubmit}
            disabled={selectedAccountIds.length === 0 || isConnecting}
            className="px-8 py-3 bg-[#50C878] text-white font-bold rounded-xl shadow-lg shadow-green-100 hover:scale-[1.02] active:scale-95 disabled:opacity-50 disabled:hover:scale-100 disabled:cursor-not-allowed transition-all flex items-center gap-2"
          >
            {isConnecting && <Loader2 className="w-4 h-4 animate-spin" />}
            {isConnecting ? 'Connecting...' : 'Connect Selected'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default AccountSelectionModal;
