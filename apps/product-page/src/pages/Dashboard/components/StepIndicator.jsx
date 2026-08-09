import React from 'react';
import { CheckCircle2 } from 'lucide-react';

const StepIndicator = ({ dashboardStep }) => {
  const steps = [
    { id: 'brief', label: 'Brief', step: 1 },
    { id: 'generate', label: 'Generate', step: 2 },
    { id: 'review', label: 'Review', step: 3 },
    { id: 'publish', label: 'Publish', step: 4 },
  ];

  // Normalize the dashboard step into a "logical step index" so we can map
  // both flows onto the same 4-step indicator:
  //   • Brief screen:      step 1 active
  //   • Strategy plan:     step 2 active   ('plan')
  //   • Single-post review OR strategy approve queue: step 3 active
  //     ('review' OR 'schedule_review')
  //   • Single-post publish UI:                      step 4 active
  //     ('publish')
  let logicalStep = 1;
  if (dashboardStep === 'brief') logicalStep = 1;
  else if (dashboardStep === 'plan') logicalStep = 2;
  else if (dashboardStep === 'review' || dashboardStep === 'schedule_review') logicalStep = 3;
  else if (dashboardStep === 'publish') logicalStep = 4;

  return (
    <div className="flex items-center justify-center gap-1.5 mb-2">
      {steps.map((s, idx) => (
        <React.Fragment key={s.id}>
          <div className="flex items-center gap-2">
            <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-semibold transition-all ${
              s.step <= logicalStep
                ? 'bg-[#F55600] text-white shadow-md shadow-orange-100'
                : 'bg-slate-100 text-slate-500 border border-slate-200'
            }`}>
              {s.step < logicalStep
                ? <CheckCircle2 className="w-3 h-3" strokeWidth={3} />
                : s.step}
            </div>
            <span className={`text-[10px] font-semibold tracking-tight uppercase ${
              s.step === logicalStep
                ? 'text-[#2B2926] font-semibold'
                : 'text-slate-500'
            }`}>
              {s.label}
            </span>
          </div>
          {idx < 3 && <div className={`w-8 h-[1.5px] mx-1 transition-all duration-500 rounded-full ${
            idx + 1 < logicalStep
              ? 'bg-[#F55600]'
              : 'bg-slate-100'
          }`}></div>}
        </React.Fragment>
      ))}
    </div>
  );
};

export default StepIndicator;
