import React, { useEffect } from 'react';
import { Loader2 } from 'lucide-react';

const CanvaCallback = () => {
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const designId = params.get('design_id');
    const editUrl = params.get('edit_url');
    const error = params.get('error');
    console.log("CanvaCallback: Current params:", { designId, editUrl, error });

    if (error) {
      console.error('Canva Error:', error);
      if (window.opener) {
        window.opener.postMessage({ type: 'CANVA_ERROR', error }, "*");
      }
      return;
    }

    if (editUrl) {
      // For this flow, we redirect to the edit_url to let the user design
      console.log("CanvaCallback: Redirecting to edit_url:", editUrl);
      window.location.href = editUrl;
    } else if (designId) {
       // If we're coming back after the design is done (Return URL)
        console.log("CanvaCallback: Design done! Sending CANVA_FINISHED for designId:", designId);
        if (window.opener) {
          window.opener.postMessage({ type: 'CANVA_FINISHED', designId }, "*");
          window.close();
        } else {
          console.warn("CanvaCallback: window.opener is missing!");
        }
    }
  }, []);

  const params = new URLSearchParams(window.location.search);
  const editUrl = params.get('edit_url');

  return (
    <div className="h-screen w-full flex flex-col items-center justify-center bg-slate-50 gap-4 p-8 text-center">
      <Loader2 className="w-10 h-10 text-[#F55600] animate-spin" />
      <p className="font-bold text-slate-600 text-lg">Connecting to Canva...</p>
      {editUrl && (
        <div className="mt-4">
          <p className="text-sm text-slate-500 mb-4">If you are not redirected automatically, click below:</p>
          <a 
            href={editUrl}
            className="px-6 py-3 bg-[#F55600] text-white rounded-xl font-bold hover:bg-[#e85a24] transition-colors"
          >
            Open Canva Editor
          </a>
        </div>
      )}
    </div>
  );
};

export default CanvaCallback;
