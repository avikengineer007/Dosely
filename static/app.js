/**
 * Dosely — Prescription Assistant App Controller
 * Conforms strictly to CSP ('script-src self', no inline handlers).
 */

(function () {
  const fileInput = document.getElementById("file-input");
  const uploadZone = document.getElementById("upload-zone");
  const root = document.getElementById("root");

  if (!fileInput || !uploadZone) return;

  // Handle drag and drop styling
  uploadZone.addEventListener("dragover", function (e) {
    e.preventDefault();
    uploadZone.style.borderColor = "var(--red)";
    uploadZone.style.background = "rgba(225, 6, 0, 0.05)";
  });

  uploadZone.addEventListener("dragleave", function (e) {
    e.preventDefault();
    uploadZone.style.borderColor = "var(--border)";
    uploadZone.style.background = "transparent";
  });

  uploadZone.addEventListener("drop", function (e) {
    e.preventDefault();
    uploadZone.style.borderColor = "var(--border)";
    uploadZone.style.background = "transparent";
    if (e.dataTransfer && e.dataTransfer.files.length > 0) {
      handleFileUpload(e.dataTransfer.files[0]);
    }
  });

  fileInput.addEventListener("change", function () {
    if (fileInput.files && fileInput.files.length > 0) {
      handleFileUpload(fileInput.files[0]);
    }
  });

  function getSubFromToken(token) {
    try {
      if (!token) return null;
      const parts = token.split(".");
      if (parts.length >= 2) {
        const payload = JSON.parse(atob(parts[1]));
        return payload.sub || null;
      }
    } catch (_) {}
    return null;
  }

  async function getAuthToken(forceRefresh = false) {
    let token = sessionStorage.getItem("dosely_auth_token");
    if (!forceRefresh && token) return token;

    // Preserve the user ID across token refreshes to keep prescription ownership consistent
    let username = getSubFromToken(token) || sessionStorage.getItem("dosely_patient_id");
    if (!username) {
      username = "patient_" + Math.random().toString(36).substring(2, 9);
    }
    sessionStorage.setItem("dosely_patient_id", username);

    try {
      const res = await fetch("/auth/token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username }),
      });
      if (res.ok) {
        const data = await res.json();
        token = data.access_token;
        sessionStorage.setItem("dosely_auth_token", token);
        return token;
      }
    } catch (err) {
      console.error("Auth error:", err);
    }
    return null;
  }

  async function authFetch(url, options = {}) {
    let token = await getAuthToken();
    const opts = { ...options };
    opts.headers = { ...(options.headers || {}) };
    if (token) {
      opts.headers["Authorization"] = `Bearer ${token}`;
    }

    let res = await fetch(url, opts);

    // If 401 Unauthorized (e.g. server restart, invalid token signature, expired token)
    if (res.status === 401) {
      console.warn("Auth token expired or invalid signature. Refreshing token and retrying request...");
      sessionStorage.removeItem("dosely_auth_token");
      token = await getAuthToken(true);
      if (token) {
        opts.headers["Authorization"] = `Bearer ${token}`;
        res = await fetch(url, opts);
      }
    }

    return res;
  }

  async function compressImageIfNeeded(file) {
    const MAX_SERVERLESS_BYTES = 3.5 * 1024 * 1024;
    if (!file || file.size <= MAX_SERVERLESS_BYTES || !file.type.startsWith("image/")) {
      return file;
    }
    return new Promise((resolve) => {
      const img = new Image();
      const url = URL.createObjectURL(file);
      img.onload = function () {
        URL.revokeObjectURL(url);
        const MAX_DIM = 2400;
        let width = img.width;
        let height = img.height;
        if (width > MAX_DIM || height > MAX_DIM) {
          if (width > height) {
            height = Math.round((height * MAX_DIM) / width);
            width = MAX_DIM;
          } else {
            width = Math.round((width * MAX_DIM) / height);
            height = MAX_DIM;
          }
        }
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, width, height);
        canvas.toBlob(
          function (blob) {
            if (blob && blob.size < file.size) {
              const newName = file.name.replace(/\.[^.]+$/, "") + ".jpg";
              resolve(new File([blob], newName, { type: "image/jpeg" }));
            } else {
              resolve(file);
            }
          },
          "image/jpeg",
          0.85
        );
      };
      img.onerror = function () {
        URL.revokeObjectURL(url);
        resolve(file);
      };
      img.src = url;
    });
  }

  async function handleFileUpload(file) {
    if (!file) return;

    // Show loading state
    root.innerHTML = `
      <h1>Analyzing Prescription...</h1>
      <p style="color:var(--text-muted)">Validating image, extracting medicine details, and checking safety rules...</p>
      <div style="margin:40px 0;text-align:center">
        <div style="display:inline-block;width:40px;height:40px;border:3px solid var(--border);border-top-color:var(--red);border-radius:50%;animation:spin 1s linear infinite"></div>
        <p style="margin-top:16px;color:var(--text-muted);font-size:14px">Processing file: ${escapeHtml(file.name)}</p>
      </div>
      <style>@keyframes spin { to { transform: rotate(360deg); } }</style>
    `;

    try {
      const processedFile = await compressImageIfNeeded(file);
      const formData = new FormData();
      formData.append("file", processedFile);

      const res = await authFetch("/prescriptions/upload", {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        let errMsg = "Upload failed (HTTP " + res.status + ")";
        try {
          const errData = await res.json();
          errMsg = errData.detail || errMsg;
        } catch (_) {}
        throw new Error(errMsg);
      }

      const rxData = await res.json();
      renderConfirmScreen(rxData);
    } catch (err) {
      renderError(err.message);
    }
  }

  function renderError(message) {
    root.innerHTML = `
      <h1 style="color:#ef4444">Upload Error</h1>
      <p style="color:var(--text-muted);margin:16px 0">${escapeHtml(message)}</p>
      <button class="btn" id="retry-btn">Try Another Prescription</button>
    `;
    document.getElementById("retry-btn").addEventListener("click", function () {
      window.location.reload();
    });
  }

  async function renderConfirmScreen(rx) {
    const rxId = rx.id;
    const extractedData = rx.extracted_data || rx.prescription || {};
    let medicines = extractedData.medicines || [];

    // Fetch original prescription image preview
    let previewUrl = null;
    let isPdf = false;
    try {
      const imgRes = await authFetch(`/prescriptions/${rxId}/image`);
      if (imgRes.ok) {
        const mime = imgRes.headers.get("content-type") || "";
        isPdf = mime.includes("pdf");
        const blob = await imgRes.blob();
        previewUrl = URL.createObjectURL(blob);
      }
    } catch (e) {
      console.warn("Could not load preview image:", e);
    }

    function renderView() {
      const hasMeds = medicines.length > 0;

      let previewElementHtml = "";
      if (previewUrl) {
        if (isPdf) {
          previewElementHtml = `
            <div style="border:1px solid var(--border);border-radius:8px;overflow:hidden;background:#000;height:420px;display:flex;flex-direction:column">
              <iframe src="${previewUrl}" style="width:100%;height:100%;border:none"></iframe>
              <div style="padding:8px 12px;background:#1e293b;border-top:1px solid var(--border);text-align:right">
                <a href="${previewUrl}" target="_blank" rel="noopener" style="color:var(--red);text-decoration:none;font-size:12px;font-weight:600">Open Full PDF ↗</a>
              </div>
            </div>
          `;
        } else {
          previewElementHtml = `
            <div style="border:1px solid var(--border);border-radius:8px;overflow:hidden;background:#000;display:flex;flex-direction:column">
              <div style="max-height:420px;overflow:auto;display:flex;justify-content:center;align-items:center;padding:8px">
                <img src="${previewUrl}" alt="Original Prescription Preview" style="max-width:100%;height:auto;border-radius:4px;display:block">
              </div>
              <div style="padding:8px 12px;background:#1e293b;border-top:1px solid var(--border);text-align:right">
                <a href="${previewUrl}" target="_blank" rel="noopener" style="color:var(--red);text-decoration:none;font-size:12px;font-weight:600">Open Full Image ↗</a>
              </div>
            </div>
          `;
        }
      } else {
        previewElementHtml = `
          <div style="border:1px dashed var(--border);border-radius:8px;padding:32px 16px;text-align:center;color:var(--text-muted);font-size:13px">
            Prescription preview not available
          </div>
        `;
      }

      let medsListHtml = "";
      if (!hasMeds) {
        const reviewSummary = extractedData.review_summary || [];
        const hasApiError = reviewSummary.some(s => s.toLowerCase().includes("api error") || s.toLowerCase().includes("badrequesterror"));
        const errorDetail = hasApiError
          ? "Anthropic Claude Vision API credit balance is too low ($0 balance). The model could not run to transcribe handwriting."
          : "The handwriting in this image could not be transcribed with high confidence.";

        medsListHtml = `
          <div style="background:rgba(245, 158, 11, 0.08);border:1px dashed #f59e0b;border-radius:8px;padding:20px;text-align:center;margin-bottom:16px">
            <p style="color:#fbbf24;font-size:14px;font-weight:600;margin-bottom:8px">No medicines detected automatically</p>
            <p style="color:#ef4444;font-size:12px;margin-bottom:10px;background:rgba(239, 68, 68, 0.1);padding:6px 10px;border-radius:6px;display:inline-block">
              ⚠️ ${escapeHtml(errorDetail)}
            </p>
            <p style="color:var(--text-muted);font-size:13px;margin-bottom:14px">
              Look at the prescription preview on the left and click <strong>"+ Add Medicine"</strong> below to enter the medicines.
            </p>
          </div>
        `;
      } else {
        medsListHtml = medicines
          .map((m, idx) => {
            const needsReview = m.needs_review || false;
            const bg = needsReview ? "rgba(245, 158, 11, 0.08)" : "rgba(255, 255, 255, 0.03)";
            const border = needsReview ? "#f59e0b" : "var(--border)";
            const badge = needsReview
              ? `<span style="background:#f59e0b;color:#000;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700">Needs Review</span>`
              : `<span style="background:#10b981;color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700">Verified</span>`;

            const ingredientsStr = m.ingredients && m.ingredients.length > 1
              ? `<div style="font-size:12px;color:var(--text-muted);margin:4px 0 8px">Contains: ${escapeHtml(m.ingredients.map(i => i.name).join(" + "))}</div>`
              : "";

            const currentForm = (m.form || "").toLowerCase();
            const formOptions = [
              { val: "tablet", label: "Tablet" },
              { val: "capsule", label: "Capsule" },
              { val: "syrup", label: "Syrup" },
              { val: "suspension", label: "Suspension" },
              { val: "injection", label: "Injection" },
              { val: "drops", label: "Drops" },
              { val: "ointment", label: "Ointment" },
              { val: "cream", label: "Cream" },
              { val: "inhaler", label: "Inhaler" },
              { val: "other", label: "Other" }
            ];

            let inferredForm = currentForm;
            if (!inferredForm || inferredForm === "unknown") {
              const nl = (m.name_raw || "").toLowerCase();
              if (/\b(tab|tablet|tabs)\b/.test(nl)) inferredForm = "tablet";
              else if (/\b(cap|capsule|caps)\b/.test(nl)) inferredForm = "capsule";
              else if (/\b(syp|syrup)\b/.test(nl)) inferredForm = "syrup";
              else if (/\b(inj|injection)\b/.test(nl)) inferredForm = "injection";
              else if (/\b(drops?)\b/.test(nl)) inferredForm = "drops";
              else if (/\b(oint|ointment|cream)\b/.test(nl)) inferredForm = "ointment";
              else inferredForm = "tablet";
            }

            let inferredStrength = m.strength || "";
            if (!inferredStrength) {
              const sm = (m.name_raw || "").match(/\b(\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|iu|%))\b/i);
              if (sm) inferredStrength = sm[1];
            }

            return `
              <div class="med-card" style="background:${bg};border:1px solid ${border};border-radius:8px;padding:14px;margin-bottom:12px">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
                  <span style="font-weight:600;font-size:15px">${escapeHtml(m.name_raw || "New Medicine")}</span>
                  <div style="display:flex;gap:8px;align-items:center">
                    ${badge}
                    <button type="button" class="remove-med-btn" data-idx="${idx}" style="background:transparent;border:none;color:#94a3b8;cursor:pointer;font-size:16px;padding:0 4px" title="Remove medicine">✕</button>
                  </div>
                </div>
                ${ingredientsStr}
                <div style="display:grid;grid-template-columns:1.4fr 1fr;gap:10px;margin-bottom:8px">
                  <div>
                    <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px">Medicine Name</label>
                    <input type="text" class="med-name-input" data-idx="${idx}" value="${escapeHtml(m.name_raw || "")}" placeholder="e.g. Lisinopril" style="width:100%;background:#0f172a;border:1px solid var(--border);color:#fff;padding:8px;border-radius:6px;box-sizing:border-box;font-size:13px">
                  </div>
                  <div>
                    <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px">Frequency / Schedule</label>
                    <input type="text" class="med-freq-input" data-idx="${idx}" value="${escapeHtml(m.frequency_raw || "")}" placeholder="e.g. BD, once daily, 1-0-1" style="width:100%;background:#0f172a;border:1px solid var(--border);color:#fff;padding:8px;border-radius:6px;box-sizing:border-box;font-size:13px">
                  </div>
                </div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
                  <div>
                    <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px">Medicine Type (Form)</label>
                    <select class="med-form-select" data-idx="${idx}" style="width:100%;background:#0f172a;border:1px solid var(--border);color:#fff;padding:8px;border-radius:6px;box-sizing:border-box;font-size:13px">
                      ${formOptions.map(opt => `<option value="${opt.val}" ${inferredForm === opt.val ? "selected" : ""}>${opt.label}</option>`).join("")}
                    </select>
                  </div>
                  <div>
                    <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px">Strength</label>
                    <input type="text" class="med-strength-input" data-idx="${idx}" value="${escapeHtml(inferredStrength)}" placeholder="e.g. 500mg, 10mg" style="width:100%;background:#0f172a;border:1px solid var(--border);color:#fff;padding:8px;border-radius:6px;box-sizing:border-box;font-size:13px">
                  </div>
                </div>
              </div>
            `;
          })
          .join("");
      }

      root.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
          <div>
            <h1 style="margin:0;font-size:24px">Confirm Prescription</h1>
            <p style="color:var(--text-muted);font-size:13px;margin-top:4px">Compare the original prescription with the medicine fields before scheduling.</p>
          </div>
          <span style="font-size:12px;color:var(--text-muted)">ID: ${escapeHtml(rxId.substring(0, 8))}</span>
        </div>

        <div style="display:grid;grid-template-columns:minmax(280px, 1fr) minmax(320px, 1.2fr);gap:24px;margin-bottom:24px;align-items:start">
          <div>
            <div style="font-weight:600;font-size:14px;margin-bottom:8px;display:flex;align-items:center;gap:6px">
              <span>📄 Original Prescription Preview</span>
            </div>
            ${previewElementHtml}
          </div>

          <div>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
              <span style="font-weight:600;font-size:14px">💊 Extracted Medicines (${medicines.length})</span>
              <button type="button" id="add-med-btn" style="background:#1e293b;border:1px solid var(--border);color:#fff;padding:4px 10px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">+ Add Medicine</button>
            </div>
            <div id="meds-container">
              ${medsListHtml}
            </div>
          </div>
        </div>

        <div style="border-top:1px solid var(--border);padding-top:18px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px">
          <div style="display:flex;gap:12px;align-items:center">
            <button class="btn" id="confirm-submit-btn" ${!hasMeds ? 'disabled style="opacity:0.5;cursor:not-allowed"' : ''}>Confirm & Generate Schedule</button>
            <button id="cancel-btn" style="background:transparent;border:1px solid var(--border);color:var(--text-muted);padding:12px 20px;border-radius:8px;cursor:pointer">Cancel</button>
          </div>
          ${!hasMeds ? '<span style="font-size:12px;color:#f59e0b">⚠️ Add at least 1 medicine to enable confirmation</span>' : ''}
        </div>
      `;

      // Cancel button
      document.getElementById("cancel-btn").addEventListener("click", function () {
        window.location.reload();
      });

      // Add Medicine button
      document.getElementById("add-med-btn").addEventListener("click", function () {
        syncInputs();
        medicines.push({
          name_raw: "",
          form: "tablet",
          strength: "",
          frequency_raw: "",
          needs_review: true,
        });
        renderView();
      });

      // Remove Medicine buttons
      document.querySelectorAll(".remove-med-btn").forEach(btn => {
        btn.addEventListener("click", function () {
          const idx = parseInt(this.getAttribute("data-idx"), 10);
          syncInputs();
          medicines.splice(idx, 1);
          renderView();
        });
      });

      // Confirm Submit
      const submitBtn = document.getElementById("confirm-submit-btn");
      if (submitBtn && hasMeds) {
        submitBtn.addEventListener("click", async function () {
          syncInputs();

          // Filter out completely blank entries
          const validMedicines = medicines
            .filter(m => m.name_raw && m.name_raw.trim().length > 0)
            .map(m => ({
              name_raw: m.name_raw.trim(),
              form: (m.form || "").trim() || undefined,
              strength: (m.strength || "").trim() || undefined,
              frequency_raw: (m.frequency_raw || "").trim(),
            }));

          if (validMedicines.length === 0) {
            alert("Please enter at least one medicine name.");
            return;
          }

          submitBtn.disabled = true;
          submitBtn.innerText = "Confirming...";

          try {
            const confirmRes = await authFetch(`/prescriptions/${rxId}/confirm`, {
              method: "PUT",
              headers: {
                "Content-Type": "application/json",
              },
              body: JSON.stringify({ medicines: validMedicines }),
            });

            if (!confirmRes.ok) {
              let errMsg = "Confirmation failed (HTTP " + confirmRes.status + ")";
              try {
                const errData = await confirmRes.json();
                if (errData.detail) {
                  errMsg = Array.isArray(errData.detail)
                    ? errData.detail.map(d => (d.loc ? d.loc.join(".") + ": " : "") + d.type).join(", ")
                    : errData.detail;
                }
              } catch (_) {}
              throw new Error(errMsg);
            }

            // Fetch generated schedule
            const schedRes = await authFetch(`/prescriptions/${rxId}/schedule`);

            if (!schedRes.ok) {
              throw new Error("Could not fetch schedule (HTTP " + schedRes.status + ")");
            }

            const scheduleData = await schedRes.json();
            renderScheduleScreen(scheduleData);
          } catch (err) {
            alert("Error: " + err.message);
            submitBtn.disabled = false;
            submitBtn.innerText = "Confirm & Generate Schedule";
          }
        });
      }
    }

    function syncInputs() {
      const nameInputs = document.querySelectorAll(".med-name-input");
      const formInputs = document.querySelectorAll(".med-form-select");
      const strengthInputs = document.querySelectorAll(".med-strength-input");
      const freqInputs = document.querySelectorAll(".med-freq-input");
      nameInputs.forEach((input, i) => {
        if (medicines[i]) {
          medicines[i].name_raw = input.value;
          medicines[i].form = formInputs[i] ? formInputs[i].value : (medicines[i].form || "tablet");
          medicines[i].strength = strengthInputs[i] ? strengthInputs[i].value : (medicines[i].strength || "");
          medicines[i].frequency_raw = freqInputs[i] ? freqInputs[i].value : "";
        }
      });
    }

    renderView();
  }

  function renderScheduleScreen(sched) {
    const timeline = sched.timeline || sched.schedule || (Array.isArray(sched) ? sched : []);

    // Collect any item-level or top-level warnings
    const warnings = [...(sched.warnings || [])];
    timeline.forEach(t => {
      if (t.warnings && Array.isArray(t.warnings)) {
        t.warnings.forEach(w => {
          if (!warnings.includes(w)) warnings.push(w);
        });
      }
    });

    let warningsHtml = "";
    if (warnings.length > 0) {
      warningsHtml = `
        <div style="background:rgba(239, 68, 68, 0.1);border:1px solid #ef4444;border-radius:8px;padding:16px;margin-bottom:24px">
          <h3 style="margin:0 0 8px;color:#ef4444;font-size:15px">⚠️ Safety & Interaction Warnings</h3>
          <ul style="margin:0;padding-left:20px;color:#fca5a5;font-size:13px">
            ${warnings.map(w => `<li style="margin-bottom:4px">${escapeHtml(w.message || w)}</li>`).join("")}
          </ul>
        </div>
      `;
    }

    let itemsHtml = "";
    if (timeline.length === 0) {
      itemsHtml = `<p style="color:var(--text-muted)">No scheduled doses generated.</p>`;
    } else {
      itemsHtml = timeline
        .map(t => {
          const drugName = t.drug_name || t.drug || "Prescription item";
          const timeSlot = t.time || "--:--";
          const instruction = t.instruction || "";
          const isSuggested = t.source === "default" || t.suggested;
          const form = t.form || t.dosage_form || "";
          const strength = t.strength || "";

          let metaBadges = [];
          if (form && form.toLowerCase() !== "unknown") {
            metaBadges.push(`<span style="background:rgba(59, 130, 246, 0.15);color:#60a5fa;border:1px solid rgba(59, 130, 246, 0.3);font-size:11px;font-weight:600;padding:2px 8px;border-radius:12px;text-transform:capitalize">💊 ${escapeHtml(form)}</span>`);
          }
          if (strength) {
            metaBadges.push(`<span style="background:rgba(148, 163, 184, 0.12);color:#cbd5e1;border:1px solid rgba(148, 163, 184, 0.25);font-size:11px;font-weight:600;padding:2px 8px;border-radius:12px">${escapeHtml(strength)}</span>`);
          }

          return `
            <div style="display:flex;align-items:center;background:#1e293b;border:1px solid var(--border);border-radius:8px;padding:14px 18px;margin-bottom:10px">
              <div style="font-weight:700;font-size:16px;color:var(--red);min-width:85px">${escapeHtml(timeSlot)}</div>
              <div style="flex:1;margin-left:16px">
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                  <span style="font-weight:600;font-size:15px;text-transform:capitalize">${escapeHtml(drugName)}</span>
                  ${metaBadges.join(" ")}
                </div>
                <div style="color:var(--text-muted);font-size:13px;margin-top:4px">${escapeHtml(instruction)}</div>
              </div>
              ${isSuggested ? `<span style="font-size:11px;background:#334155;color:#94a3b8;padding:2px 6px;border-radius:4px">Suggested</span>` : ""}
            </div>
          `;
        })
        .join("");
    }

    root.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <h1 style="margin:0;font-size:24px">Medication Schedule</h1>
        <button id="new-rx-btn" class="btn" style="padding:8px 16px;margin:0;font-size:13px">Upload Another</button>
      </div>
      <p style="color:var(--text-muted);font-size:14px;margin-bottom:20px">
        Your personalized daily dose schedule. All times and instructions are derived conservatively.
      </p>
      ${warningsHtml}
      <div style="margin-bottom:24px">
        ${itemsHtml}
      </div>
    `;

    document.getElementById("new-rx-btn").addEventListener("click", function () {
      window.location.reload();
    });
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
})();
