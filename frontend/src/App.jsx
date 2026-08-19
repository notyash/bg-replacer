import React, { useState, useEffect, useRef } from 'react';

function App() {
  // Config States (cached in LocalStorage)
  const [comfyAddress, setComfyAddress] = useState(() => 
    localStorage.getItem('bg_comfy_address') || '127.0.0.1:8188'
  );
  const [comfyMode, setComfyMode] = useState(() => 
    localStorage.getItem('bg_comfy_mode') || 'upload'
  );
  const [geminiKey, setGeminiKey] = useState(() => 
    localStorage.getItem('bg_gemini_key') || ''
  );
  const [showSettings, setShowSettings] = useState(() => 
    !localStorage.getItem('bg_gemini_key')
  );
  const [showKey, setShowKey] = useState(false);

  // Input States
  const [file, setFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [bgDescription, setBgDescription] = useState('');
  const [dragActive, setDragActive] = useState(false);

  // Pipeline execution states
  const [generating, setGenerating] = useState(false);
  const [status, setStatus] = useState('');
  const [progress, setProgress] = useState(0);
  const [elapsedTime, setElapsedTime] = useState(0);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  // Draggable comparison slider states
  const [sliderPosition, setSliderPosition] = useState(50);
  const [isSliderDragging, setIsSliderDragging] = useState(false);
  const sliderContainerRef = useRef(null);
  const timerRef = useRef(null);

  // Save config changes
  useEffect(() => {
    localStorage.setItem('bg_comfy_address', comfyAddress);
  }, [comfyAddress]);

  useEffect(() => {
    localStorage.setItem('bg_comfy_mode', comfyMode);
  }, [comfyMode]);

  useEffect(() => {
    localStorage.setItem('bg_gemini_key', geminiKey);
  }, [geminiKey]);

  // Clean up timer
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  // Handle image upload and thumbnail creation
  const handleFileChange = (selectedFile) => {
    if (selectedFile) {
      setFile(selectedFile);
      const url = URL.createObjectURL(selectedFile);
      setPreviewUrl(url);
      setResult(null);
      setError(null);
    }
  };

  const handleDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileChange(e.dataTransfer.files[0]);
    }
  };

  const removeFile = (e) => {
    e.stopPropagation();
    setFile(null);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(null);
    setResult(null);
  };

  // Draggable slider interaction
  const updateSlider = (clientX) => {
    if (!sliderContainerRef.current) return;
    const rect = sliderContainerRef.current.getBoundingClientRect();
    const x = clientX - rect.left;
    const pct = Math.max(0, Math.min(100, (x / rect.width) * 100));
    setSliderPosition(pct);
  };

  const handlePointerDown = (e) => {
    setIsSliderDragging(true);
    updateSlider(e.clientX);
    sliderContainerRef.current.setPointerCapture(e.pointerId);
  };

  const handlePointerMove = (e) => {
    if (isSliderDragging) {
      updateSlider(e.clientX);
    }
  };

  const handlePointerUp = (e) => {
    setIsSliderDragging(false);
    if (sliderContainerRef.current) {
      sliderContainerRef.current.releasePointerCapture(e.pointerId);
    }
  };

  // Start Generation
  const handleGenerate = async () => {
    if (!file || !bgDescription.trim()) return;

    setGenerating(true);
    setStatus('Initializing pipeline...');
    setProgress(5);
    setElapsedTime(0);
    setResult(null);
    setError(null);

    // Start timer
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(() => {
      setElapsedTime(prev => prev + 1);
    }, 1000);

    const formData = new FormData();
    formData.append('file', file);
    formData.append('background_description', bgDescription);
    formData.append('comfyui_server_address', comfyAddress);
    formData.append('comfyui_mode', comfyMode);
    formData.append('gemini_api_key', geminiKey);

    // Determine target host URL
    // If we are served statically by backend, use the same host, otherwise use local uvicorn host
    const backendUrl = import.meta.env.DEV ? 'http://localhost:8000/api/generate' : '/api/generate';

    try {
      const response = await fetch(backendUrl, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const errorJson = await response.json().catch(() => ({}));
        throw new Error(errorJson.error || `HTTP error ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop(); // save incomplete line

        for (const line of lines) {
          const trimmed = line.trim();
          if (trimmed.startsWith('data: ')) {
            const dataStr = trimmed.slice(6);
            try {
              const data = JSON.parse(dataStr);
              if (data.error) {
                throw new Error(data.error);
              }
              if (data.status) {
                setStatus(data.status);
              }
              if (data.progress !== undefined) {
                setProgress(data.progress);
              }
              if (data.result) {
                setResult(data.result);
                setGenerating(false);
                clearInterval(timerRef.current);
              }
            } catch (e) {
              if (e instanceof SyntaxError) {
                // Ignore parsing errors of incomplete JSON frames
                continue;
              }
              throw e;
            }
          }
        }
      }
    } catch (err) {
      console.error(err);
      setError(err.message || 'An unexpected connection error occurred.');
      setGenerating(false);
      clearInterval(timerRef.current);
    }
  };

  const handleDownload = () => {
    if (!result || !result.output_url) return;
    
    // We download base64 directly if available, otherwise open static URL
    const link = document.createElement('a');
    link.href = result.output_base64 || result.output_url;
    link.download = `bg_replaced_${Date.now()}.png`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const formatTime = (secs) => {
    const mins = Math.floor(secs / 60);
    const s = secs % 60;
    return mins > 0 ? `${mins}m ${s}s` : `${s}s`;
  };

  const isFormValid = file && bgDescription.trim() && geminiKey.trim() && comfyAddress.trim();

  return (
    <div className="app-container">
      <header className="app-header">
        <div className="logo-container">
          <div className="logo-icon">AG</div>
          <div>
            <h1 className="app-title">AI Background Replacer</h1>
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Powered by Gemini & ComfyUI</p>
          </div>
        </div>
        <button 
          className={`btn-settings-toggle ${showSettings ? 'active' : ''}`}
          onClick={() => setShowSettings(!showSettings)}
          aria-expanded={showSettings}
        >
          <svg width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
          Settings
        </button>
      </header>

      {/* Settings Section */}
      <section className={`settings-drawer glass-card ${showSettings ? 'open' : ''}`}>
        <div className="settings-grid">
          <div className="form-group">
            <label htmlFor="comfy_address">
              ComfyUI Server Address
            </label>
            <input 
              id="comfy_address" 
              type="text" 
              value={comfyAddress}
              onChange={(e) => setComfyAddress(e.target.value)}
              placeholder="e.g. 127.0.0.1:8188"
            />
            <span className="form-helper">The host:port address of your ComfyUI workspace.</span>
          </div>

          <div className="form-group">
            <label htmlFor="comfy_mode">
              ComfyUI Mode
            </label>
            <select 
              id="comfy_mode"
              value={comfyMode}
              onChange={(e) => setComfyMode(e.target.value)}
            >
              <option value="upload">Local Upload (Multipart)</option>
              <option value="url">Direct URL (SeaArt / Cloud)</option>
            </select>
            <span className="form-helper">Upload (local instance) or URL (SeaArt API instances).</span>
          </div>

          <div className="form-group">
            <label htmlFor="gemini_key">
              Gemini API Key
              <button 
                type="button"
                style={{ background: 'none', border: 'none', color: 'var(--accent-color)', cursor: 'pointer', fontSize: '0.75rem' }}
                onClick={() => setShowKey(!showKey)}
              >
                {showKey ? 'Hide' : 'Show'}
              </button>
            </label>
            <input 
              id="gemini_key"
              type={showKey ? 'text' : 'password'}
              value={geminiKey}
              onChange={(e) => setGeminiKey(e.target.value)}
              placeholder="Enter your Gemini API key"
            />
            <span className="form-helper">Used only client-side to prompt background details.</span>
          </div>
        </div>
      </section>

      <main className="main-grid">
        {/* Input Panel */}
        <section className="panel-input">
          {/* File Upload Zone */}
          <div 
            className={`glass-card upload-zone ${dragActive ? 'drag-active' : ''}`}
            onDragEnter={handleDrag}
            onDragOver={handleDrag}
            onDragLeave={handleDrag}
            onDrop={handleDrop}
            onClick={() => !file && document.getElementById('file-picker').click()}
          >
            <input 
              id="file-picker"
              type="file"
              accept="image/*"
              style={{ display: 'none' }}
              onChange={(e) => e.target.files && handleFileChange(e.target.files[0])}
            />
            
            {previewUrl ? (
              <div style={{ width: '100%', position: 'relative' }}>
                <img src={previewUrl} alt="Thumbnail preview" className="thumbnail-preview" />
                <button type="button" className="btn-remove-file" onClick={removeFile} title="Remove image">
                  &times;
                </button>
              </div>
            ) : (
              <>
                <svg className="upload-icon" fill="none" stroke="currentColor" strokeWidth="1.5" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V9.75m0 0l3 3m-3-3l-3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z" />
                </svg>
                <div>
                  <p className="upload-text">Drag and drop your photo here</p>
                  <p className="upload-subtext">or click to browse from files</p>
                </div>
              </>
            )}
          </div>

          {/* Description Card */}
          <div className="glass-card prompt-card">
            <h2 style={{ fontSize: '1.05rem', fontWeight: 600 }}>Describe the New Background</h2>
            <textarea 
              className="prompt-textarea"
              value={bgDescription}
              onChange={(e) => setBgDescription(e.target.value)}
              placeholder="e.g. cozy industrial coffee shop, warm golden hours light, empty tables"
              disabled={generating}
            />
            
            <button 
              className="btn-generate"
              disabled={!isFormValid || generating}
              onClick={handleGenerate}
            >
              {generating ? (
                <>
                  <div className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }}></div>
                  Generating...
                </>
              ) : (
                <>
                  <svg width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 21l3.596-3.596m1.125-1.125L17 13l-3.596-3.596m-1.125-1.125L9 9l3.596-3.596m1.125-1.125L17 5l-3.596 3.596" />
                  </svg>
                  Generate Background
                </>
              )}
            </button>
          </div>
        </section>

        {/* Preview / Results Panel */}
        <section className="panel-preview">
          {/* Progress / Loading Panel */}
          {generating && (
            <div className="glass-card progress-card animate-pulse-slow">
              <div className="spinner"></div>
              <div style={{ width: '100%' }}>
                <p className="status-text">{status}</p>
                <p className="elapsed-time">Elapsed: {formatTime(elapsedTime)}</p>
              </div>
              <div className="progress-track">
                <div className="progress-bar" style={{ width: `${progress}%` }}></div>
              </div>
            </div>
          )}

          {/* Error Notice */}
          {error && (
            <div className="error-box">
              <div className="error-title">
                <svg width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
                Generation Failed
              </div>
              <p className="error-body">{error}</p>
            </div>
          )}

          {/* Result Panel */}
          {result && (
            <div className="glass-card result-card">
              <div className="result-header">
                <h2 className="result-title">Result Preview</h2>
                <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Drag slider to compare</span>
              </div>

              {/* Before / After Slider */}
              <div 
                className="comparison-slider-wrapper"
                ref={sliderContainerRef}
                onPointerDown={handlePointerDown}
                onPointerMove={handlePointerMove}
                onPointerUp={handlePointerUp}
                style={{ '--clip-pos': `${sliderPosition}%` }}
              >
                {/* Before Image (original) */}
                <img 
                  src={previewUrl} 
                  alt="Original photo" 
                  className="comparison-image before"
                />

                {/* After Image (generated) */}
                <img 
                  src={result.output_base64 || result.output_url} 
                  alt="Background replaced photo" 
                  className="comparison-image after"
                />

                {/* Badges */}
                <span className="slider-badge after-badge">Result</span>
                <span className="slider-badge before-badge">Original</span>

                {/* Slider bar and handle */}
                <div className="slider-handle-line">
                  <div className="slider-handle-button">
                    <svg width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 21L3 16.5m0 0L7.5 12M3 16.5h13.5m0-13.5L21 7.5m0 0L16.5 12M21 7.5H7.5" />
                    </svg>
                  </div>
                </div>
              </div>

              {/* Warnings */}
              {result.warning && (
                <div className="warning-box">
                  <svg className="warning-icon" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                  <div>
                    <strong style={{ display: 'block', fontSize: '0.85rem' }}>Manual Mask Warning</strong>
                    <p style={{ margin: 0, fontSize: '0.8rem', opacity: 0.95 }}>{result.warning}</p>
                  </div>
                </div>
              )}

              {/* Action Buttons */}
              <div className="btn-group">
                <button className="btn-download" onClick={handleDownload}>
                  <svg width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
                  </svg>
                  Download HD Image
                </button>
              </div>

              {/* Collapsible Generation details */}
              <details style={{ marginTop: '0.5rem' }}>
                <summary className="details-summary">View pipeline configuration & prompt details</summary>
                <div className="details-content">
                  <div className="detail-row">
                    <span className="detail-label">Edge Complexity:</span>
                    <span className="detail-value">{result.analysis.edge_complexity}</span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-label">Background Contamination:</span>
                    <span className="detail-value">{result.analysis.background_contamination ? 'Yes' : 'No'}</span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-label">Lighting Direction:</span>
                    <span className="detail-value">{result.analysis.lighting_direction}</span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-label">Lighting Quality:</span>
                    <span className="detail-value">{result.analysis.lighting_quality}</span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-label">Reflective Surface Detected:</span>
                    <span className="detail-value">{result.analysis.reflective_surface_detected ? 'Yes' : 'No'}</span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-label">Grow Subject Mask (negated):</span>
                    <span className="detail-value">-{result.analysis.recommended_bg_mask_grow_px} px</span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-label">Feather Subject Mask:</span>
                    <span className="detail-value">{result.analysis.recommended_feather_px} px</span>
                  </div>
                  
                  <div style={{ marginTop: '0.5rem' }}>
                    <span className="detail-label" style={{ display: 'block', marginBottom: '0.25rem' }}>Expanded Positive Prompt:</span>
                    <div className="prompt-details-box">{result.positive_prompt}</div>
                  </div>
                  <div style={{ marginTop: '0.5rem' }}>
                    <span className="detail-label" style={{ display: 'block', marginBottom: '0.25rem' }}>Expanded Negative Prompt:</span>
                    <div className="prompt-details-box">{result.negative_prompt}</div>
                  </div>
                </div>
              </details>
            </div>
          )}

          {/* Idle Placeholder */}
          {!generating && !result && !error && (
            <div className="glass-card" style={{ padding: '3rem 2rem', textAlign: 'center', color: 'var(--text-muted)', borderStyle: 'dashed' }}>
              <svg width="48" height="48" fill="none" stroke="currentColor" strokeWidth="1" viewBox="0 0 24 24" style={{ margin: '0 auto 1rem', opacity: 0.4 }}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 15.75l5.159-5.159a2.25 2.25 0 013.182 0l5.159 5.159m-1.5-1.5l1.409-1.409a2.25 2.25 0 013.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 001.5-1.5V6a1.5 1.5 0 00-1.5-1.5H3.75A1.5 1.5 0 002.25 6v12a1.5 1.5 0 001.5 1.5zm10.5-11.25h.008v.008h-.008V8.25zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0z" />
              </svg>
              <p style={{ fontWeight: 500, fontSize: '0.95rem', color: 'var(--text-secondary)' }}>No generation result yet</p>
              <p style={{ fontSize: '0.8125rem', marginTop: '0.25rem' }}>Upload a photo and describe a scene, then click Generate.</p>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

export default App;
