/*
 * Bar IA — carte Lovelace
 * Chargée automatiquement par l'intégration (aucune ressource à déclarer).
 * Usage : type: custom:bar-ia-card
 *
 * Sécurité : toutes les valeurs dynamiques (réponses IA, saisies) passent par esc()
 * avant insertion dans le DOM.
 */
(() => {
  const VERSION = "2.4.0";
  if (customElements.get("bar-ia-card")) return;

  const esc = (v) =>
    String(v ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  // Réduit une photo (appareil photo / galerie) en JPEG ; renvoie le base64 sans préfixe.
  const resizeImage = (file, maxDim, quality = 0.85) =>
    new Promise((resolve, reject) => {
      if (!file || !/^image\//.test(file.type || "image/")) return reject(new Error("Ce fichier n'est pas une image."));
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        const scale = Math.min(1, maxDim / Math.max(img.naturalWidth, img.naturalHeight));
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
        canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);
        resolve(canvas.toDataURL("image/jpeg", quality).split(",")[1]);
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Image illisible.")); };
      img.src = url;
    });

  const val = (f) => (f && typeof f === "object" && !Array.isArray(f) ? f.valeur : f);
  const fmtEur = (n) =>
    Number(n || 0).toLocaleString("fr-FR", { maximumFractionDigits: 0 }) + " €";

  const TABS = [
    ["recherche", "🔍 Recherche"],
    ["bar", "🥃 Mon bar"],
    ["plan", "🗺️ Plan"],
    ["barman", "🍸 Barman"],
    ["conseils", "📈 Conseils"],
  ];
  const CAT_LABEL = {
    rhum: "Rhum", whisky: "Whisky", gin: "Gin", vodka: "Vodka",
    tequila_mezcal: "Tequila/Mezcal", liqueurs: "Liqueurs", vin: "Vin", autres: "Autres",
  };
  const TASK_LABEL = {
    search: "Recherche de la fiche…", cocktail: "Le barman réfléchit…",
    audit: "Analyse du bar…", purchase: "Évaluation de l'achat…",
  };
  const VERDICT = {
    acheter: ["✅", "Acheter"], attendre: ["⏳", "Attendre"], eviter: ["⛔", "Éviter"],
  };
  const EDIT_FIELDS = [
    ["nom", "Nom"], ["type", "Type"], ["annee", "Année"], ["origine", "Origine (pays / région)"],
    ["marque_distillerie", "Marque / distillerie"], ["abv", "ABV (%)"], ["volume_ml", "Volume (mL)"],
    ["prix_moyen", "Prix (€)"], ["corps_cocktail", "Corps cocktail (léger / moyen / puissant)"],
    ["profil_aromatique", "Profil aromatique", true], ["cocktails", "Cocktails conseillés", true],
  ];
  const CRITERIA = [["nez", "Nez"], ["bouche", "Bouche"], ["finale", "Finale"], ["qualite_prix", "Qualité/prix"]];
  const TYPES = ["Aucun", "Rhum", "Gin", "Vodka", "Whisky", "Tequila / Mezcal", "Liqueur", "Vin"];

  class BarIACard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._data = null;
      this._error = "";
      this._busy = false;
      this._form = { coef_sucre_ml_par_g: 0.62, quantite: 1 };
      this._tab = null;
      this._selected = null; // id bouteille sélectionnée (Mon bar)
      this._editing = null; // id bouteille en cours de modification
      this._cell = null; // case sélectionnée (Plan)
      this._unsub = null;
      this._signed = {}; // chemin d'image local -> {url, t}
    }

    // URL affichable d'une image : web (https) directe, locale via chemin signé (authentifié).
    _imgSrc(img) {
      if (!img) return "";
      if (img.type === "url") return img.url;
      if (img.type !== "local" || !this._hass) return "";
      const c = this._signed[img.path];
      if (c && c.url && Date.now() - c.t < 20 * 3600 * 1000) return c.url;
      if (!c || (!c.pending && Date.now() - c.t > 5000)) {
        this._signed[img.path] = { pending: true, t: Date.now() };
        this._hass.callWS({ type: "auth/sign_path", path: img.path, expires: 86400 })
          .then((r) => { this._signed[img.path] = { url: r.path, t: Date.now() }; this._render(); })
          .catch(() => { this._signed[img.path] = { t: Date.now() }; });
      }
      return c?.url || "";
    }

    _imgTag(img, cls) {
      const src = this._imgSrc(img);
      return src
        ? `<img class="${cls}" src="${esc(src)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
        : `<div class="${cls} ph">🍾</div>`;
    }

    _renderImageBlock(s) {
      const has = !!s.image;
      return `<div class="imgblock">
          ${has ? this._imgTag(s.image, "big") : ""}
          <div class="actions">
            <label class="btn">📷 ${has ? "Changer la photo" : "Ajouter une photo"}<input type="file" accept="image/*" data-upload="${esc(s.id)}" hidden></label>
            ${has ? `<button data-act="rm-image" data-sid="${esc(s.id)}">Retirer l'image</button>` : ""}
          </div>
          <div class="actions">
            ${this._input(`u_${s.id}`, "…ou adresse web de l'image (https://)", { max: 2000, ph: "https://…" })}
            <button data-act="url-image" data-sid="${esc(s.id)}">🔗 Utiliser ce lien</button>
          </div>
        </div>`;
    }

    setConfig(config) {
      this._config = config || {};
      const visible = this._visibleTabs();
      if (!this._tab || !visible.includes(this._tab)) {
        let saved = null;
        try { saved = localStorage.getItem("bar-ia-tab"); } catch (_e) { /* ignoré */ }
        const wanted = [saved, this._config.default_tab].find((t) => t && visible.includes(t));
        this._tab = wanted || visible[0];
      }
      this._render();
    }

    // Onglets affichés : tous sauf ceux de hidden_tabs (au moins un reste visible).
    _visibleTabs() {
      const hidden = Array.isArray(this._config?.hidden_tabs) ? this._config.hidden_tabs : [];
      const visible = TABS.map(([k]) => k).filter((k) => !hidden.includes(k));
      return visible.length ? visible : [TABS[0][0]];
    }

    set hass(hass) {
      const first = !this._hass;
      this._hass = hass;
      if (first) this._subscribe();
    }

    connectedCallback() {
      if (this._hass && !this._unsub) this._subscribe();
    }

    disconnectedCallback() {
      if (this._unsub) {
        this._unsub.then((u) => u && u()).catch(() => {});
        this._unsub = null;
      }
    }

    getCardSize() {
      return 10;
    }

    static getStubConfig() {
      return {};
    }

    static getConfigElement() {
      return document.createElement("bar-ia-card-editor");
    }

    // Vue « sections » : la carte prend toute la largeur de sa section.
    getGridOptions() {
      return { columns: "full", min_columns: 6, rows: "auto" };
    }

    // Anciennes versions de HA
    getLayoutOptions() {
      return { grid_columns: "full", grid_rows: "auto" };
    }

    _subscribe() {
      if (!this._hass || this._unsub) return;
      this._unsub = this._hass.connection
        .subscribeMessage(
          (msg) => {
            this._data = msg;
            this._render();
          },
          { type: "bar_ia/subscribe" }
        )
        .catch((err) => {
          this._error = "Bar IA n'est pas configuré : ajoutez l'intégration dans Paramètres › Appareils et services. (" + (err.message || err.code || err) + ")";
          this._unsub = null;
          this._render();
        });
    }

    async _call(service, data = {}) {
      this._error = "";
      this._busy = true;
      this._render();
      try {
        await this._hass.callService("bar_ia", service, data);
      } catch (err) {
        this._error = err?.message || String(err);
      } finally {
        this._busy = false;
        this._render();
      }
    }

    _spirit(id) {
      return (this._data?.spirits || []).find((s) => s.id === id);
    }

    // ------------------------------------------------------------------ rendu
    _render() {
      if (!this.shadowRoot) return;
      const d = this._data;
      const running = d?.status === "en_cours";
      let body = "";
      if (!d) {
        body = this._error
          ? `<div class="err">${esc(this._error)}</div>`
          : `<div class="muted">Chargement…</div>`;
      } else {
        body = {
          recherche: () => this._renderSearch(d),
          bar: () => this._renderBar(d),
          plan: () => this._renderPlan(d),
          barman: () => this._renderBarman(d),
          conseils: () => this._renderAdvice(d),
        }[this._tab]?.() ?? "";
      }

      const status = !d
        ? ""
        : running
        ? `<div class="status run"><span class="spin"></span>${esc(TASK_LABEL[d.running_task] || "IA en cours…")}</div>`
        : d.status === "erreur"
        ? `<div class="status ko">⚠️ ${esc(d.last_error || "Erreur IA")}</div>`
        : "";

      this.shadowRoot.innerHTML = `
        <style>${STYLE}</style>
        <ha-card>
          <div class="head">
            <div class="title">${esc(this._config?.title ?? "🥃 Bar IA")}</div>
            ${d ? `<div class="kpi"><b>${esc(d.stats.bouteilles)}</b> btl · <b>${esc(fmtEur(d.stats.valeur))}</b></div>` : ""}
          </div>
          <nav>${TABS.filter(([k]) => this._visibleTabs().includes(k)).map(([k, l]) => `<button class="tab ${k === this._tab ? "on" : ""}" data-tab="${k}">${l}</button>`).join("")}</nav>
          ${status}
          ${this._error && d ? `<div class="err">${esc(this._error)}</div>` : ""}
          <div class="body ${this._busy ? "busy" : ""}">${body}</div>
        </ha-card>`;
      this._bind();
    }

    _input(name, label, opts = {}) {
      const v = this._form[name] ?? "";
      const type = opts.type || "text";
      const extra = type === "number" ? ` step="${opts.step ?? "any"}" min="${opts.min ?? 0}"` : ` maxlength="${opts.max ?? 255}"`;
      return `<label class="f ${opts.wide ? "wide" : ""}"><span>${esc(label)}</span>
        <input data-f="${name}" type="${type}" value="${esc(v)}" placeholder="${esc(opts.ph || "")}"${extra}></label>`;
    }

    _select(name, label, options, allowEmpty = true) {
      const v = this._form[name] ?? "";
      return `<label class="f"><span>${esc(label)}</span><select data-f="${name}">
        ${allowEmpty ? `<option value="">—</option>` : ""}
        ${options.map(([k, l]) => `<option value="${esc(k)}" ${String(k) === String(v) ? "selected" : ""}>${esc(l)}</option>`).join("")}
        </select></label>`;
    }

    _renderFiche(f, maison) {
      if (!f) return "";
      const rows = [
        ["Type", val(f.type)], ["Origine", val(f.origine)], ["Distillerie", val(f.marque_distillerie)],
        ["Année", val(f.annee)], ["ABV", val(f.abv) ? `${val(f.abv)} %` : ""],
        ["Volume", val(f.volume_ml) ? `${val(f.volume_ml)} mL` : ""],
        ["Prix moyen", val(f.prix_moyen) ? `${val(f.prix_moyen)} €` : ""],
        ["Corps cocktail", [val(f.corps_cocktail), f.corps_cocktail?.raison].filter((x) => x && x !== "-").join(" — ")],
        ["Profil aromatique", val(f.profil_aromatique)],
        ["Cocktails", val(f.cocktails)],
        ["Sources prix", f.prix_moyen?.sources],
      ].filter(([, v]) => v !== undefined && v !== null && String(v).trim() !== "" && v !== "-");
      let m = "";
      if (maison?.est_maison) {
        m = `<div class="sub">🏠 Maison — base : ${esc(maison.base_utilisee || "-")} · ABV estimé ${esc(maison.abv_final_estime_pct)} % ·
          volume ${esc(maison.volume_final_estime_ml)} mL · coût ${esc(maison.cout_total)} € · prix conseillé ${esc(maison.prix_conseille)} €</div>`;
      }
      return `<div class="fiche"><div class="fname">${esc(val(f.nom))}</div>${m}
        <table>${rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v).replace(/\n/g, "<br>")}</td></tr>`).join("")}</table></div>`;
    }

    _renderSearch(d) {
      const maison = !!this._form.maison;
      const s = d.search;
      return `
        <div class="grid">
          ${this._input("nom", "Nom du spiritueux", { wide: true, ph: "ex. Diplomatico Reserva" })}
          ${this._input("type", "Type", { ph: "Rhum, Gin…" })}
          ${this._input("annee", "Année (optionnel)", { max: 20 })}
          ${this._input("pays", "Pays d'origine (optionnel)", { max: 100, ph: "Guadeloupe, Écosse…" })}
          <label class="chk wide"><input type="checkbox" data-f="maison" ${maison ? "checked" : ""}> 🏠 Bouteille maison (calcul sans IA)</label>
          ${maison ? `
            ${this._input("base_utilisee", "Base utilisée", { max: 50 })}
            ${this._input("ingredients", "Ingrédients + coûts", { ph: "citrons 3 €, sucre 1,5 €" })}
            ${this._input("abv_base", "ABV base (%)", { type: "number", step: 0.1 })}
            ${this._input("volume_base_ml", "Alcool (mL)", { type: "number" })}
            ${this._input("volume_eau_ml", "Eau (mL)", { type: "number" })}
            ${this._input("sucre_g", "Sucre (g)", { type: "number" })}
            ${this._input("coef_sucre_ml_par_g", "Coef sucre (mL/g)", { type: "number", step: 0.01 })}
            ${this._input("prix_base_litre", "Prix base (€/L)", { type: "number", step: 0.1 })}
            ${this._input("volume_bouteille_ml", "Volume bouteille (mL)", { type: "number" })}` : ""}
        </div>
        <div class="actions">
          <button class="primary" data-act="search" ${d.status === "en_cours" ? "disabled" : ""}>${maison ? "Calculer la fiche" : "Rechercher avec l'IA"}</button>
          ${maison ? "" : `<label class="btn primary ${d.status === "en_cours" ? "off" : ""}">📷 Rechercher par photo<input type="file" accept="image/*" data-photosearch hidden ${d.status === "en_cours" ? "disabled" : ""}></label>`}
        </div>
        ${maison ? "" : `<div class="sub">Photographiez l'étiquette : l'IA identifie la bouteille. Les champs ci-dessus servent alors d'indices facultatifs.</div>`}
        ${s ? `${s.image ? this._imgTag(s.image, "big") : ""}${this._renderFiche(s.fiche, s.maison)}
          <div class="actions">
            ${this._input("quantite", "Quantité", { type: "number", min: 1 })}
            <button class="primary" data-act="add">➕ Ajouter au bar</button>
            <button data-act="clear" data-kind="search">Effacer</button>
          </div>` : ""}`;
    }

    _renderBar(d) {
      const st = d.stats;
      const cats = Object.entries(st.categories).filter(([, n]) => n > 0)
        .map(([k, n]) => `<span class="chip">${esc(CAT_LABEL[k] || k)} <b>${esc(n)}</b></span>`).join("");
      const list = d.spirits.length
        ? d.spirits.map((s) => `
          <div class="row ${s.id === this._selected ? "sel" : ""}" data-sel="${esc(s.id)}">
            ${this._imgTag(s.image, "thumb")}
            <div class="lbl">${esc(s.label)}<div class="sub">${esc(val(s.fiche.type) || "")}${s.prix ? " · " + esc(fmtEur(s.prix)) : ""}${s.degustation ? ` · <span class="note">★ ${esc(s.degustation.note)}/10</span> (${esc(s.degustation.nb)})` : ""}</div></div>
            <div class="qty">
              <button class="ic" data-adj="${esc(s.id)}" data-delta="-1" title="Retirer">−</button>
              <b>${esc(s.quantite)}</b>
              <button class="ic" data-adj="${esc(s.id)}" data-delta="1" title="Ajouter">+</button>
            </div>
          </div>
          ${s.id === this._selected ? `${this._renderImageBlock(s)}${this._editing === s.id ? this._renderEdit(s) : `${this._renderFiche(s.fiche, s.fiche.maison)}
            <div class="actions"><button data-act="edit" data-sid="${esc(s.id)}">✏️ Modifier les informations</button></div>`}
            ${this._renderTastings(s)}
            <div class="actions"><button class="danger" data-del="${esc(s.id)}">🗑️ Supprimer cette bouteille</button></div>` : ""}`).join("")
        : `<div class="muted">Le bar est vide. Utilisez l'onglet Recherche pour ajouter une bouteille.</div>`;
      return `
        <div class="stats">
          <div><span>Références</span><b>${esc(st.references)}</b></div>
          <div><span>Bouteilles</span><b>${esc(st.bouteilles)}</b></div>
          <div><span>Valeur</span><b>${esc(fmtEur(st.valeur))}</b></div>
          <div><span>🏠 Maison</span><b>${esc(st.bouteilles_maison)} · ${esc(fmtEur(st.valeur_maison))}</b></div>
          <div><span>🏭 Industriel</span><b>${esc(st.bouteilles_industrielles)} · ${esc(fmtEur(st.valeur_industrielle))}</b></div>
        </div>
        <div class="chips">${cats}</div>
        <div class="list">${list}</div>`;
    }

    _renderEdit(s) {
      return `<div class="fiche">
        <div class="fname">✏️ Modifier — ${esc(val(s.fiche.nom))}</div>
        <div class="grid">
          ${EDIT_FIELDS.map(([k, l, wide]) => wide
            ? `<label class="f wide"><span>${esc(l)}</span><textarea data-f="e_${k}" maxlength="1000" rows="3">${esc(this._form[`e_${k}`] ?? "")}</textarea></label>`
            : this._input(`e_${k}`, l, { max: 1000 })).join("")}
        </div>
        <div class="actions">
          <button class="primary" data-act="save-edit" data-sid="${esc(s.id)}">Enregistrer</button>
          <button data-act="cancel-edit">Annuler</button>
        </div>
      </div>`;
    }

    _renderTastings(s) {
      const list = (s.fiche.degustations || []).slice().sort((a, b) => String(b.date).localeCompare(String(a.date)));
      const crit = (t) => CRITERIA.filter(([k]) => t[k] !== null && t[k] !== undefined)
        .map(([k, l]) => `${esc(l)} ${esc(t[k])}`).join(" · ");
      const g = s.degustation;
      return `<div class="fiche tasting">
        <div class="fname">🍷 Dégustations${g ? ` — <span class="note">★ ${esc(g.note)}/10</span> <span class="sub">(${esc(g.nb)})</span>` : ""}</div>
        ${g ? `<div class="sub">Moyennes : ${crit(g)}</div>` : ""}
        ${list.map((t) => `<div class="trow">
            <div><b>${esc(t.date)}</b> · <span class="note">★ ${esc(t.globale)}</span> <span class="sub">${crit(t)}</span>
            ${t.commentaire ? `<div>${esc(t.commentaire)}</div>` : ""}</div>
            <button class="ic" data-deltaste="${esc(t.id)}" data-sid="${esc(s.id)}" title="Supprimer">✕</button>
          </div>`).join("")}
        <div class="grid">
          ${CRITERIA.map(([k, l]) => this._input(`t_${k}`, `${l} /10`, { type: "number", step: 0.5, min: 0 })).join("")}
          ${this._input("t_date", "Date", { type: "date" })}
          <label class="f wide"><span>Commentaire</span><textarea data-f="t_commentaire" maxlength="1000" rows="2">${esc(this._form.t_commentaire || "")}</textarea></label>
        </div>
        <div class="actions"><button class="primary" data-act="taste" data-sid="${esc(s.id)}">Enregistrer la dégustation</button></div>
      </div>`;
    }

    _renderProfile(d) {
      const cat = this._form.prof_cat || "rhum";
      const rated = d.spirits.filter((s) => s.categorie === cat && s.degustation)
        .sort((a, b) => b.degustation.note - a.degustation.note);
      const p = (d.profils || {})[cat];
      const ul = (t, arr) => (arr?.length ? `<h4>${esc(t)}</h4><ul>${arr.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "");
      return `
        <h3>🍷 Mes dégustations</h3>
        <div class="grid">${this._select("prof_cat", "Type", Object.entries(CAT_LABEL), false)}</div>
        ${rated.length ? `<table class="rank">
            <tr><th>#</th><th>Bouteille</th><th>Note</th></tr>
            ${rated.map((s, i) => `<tr><td>${i + 1}</td><td>${esc(s.label)}</td><td class="note">★ ${esc(s.degustation.note)} <span class="sub">(${esc(s.degustation.nb)})</span></td></tr>`).join("")}
          </table>
          <div class="actions"><button class="primary" data-act="profile" ${d.status === "en_cours" ? "disabled" : ""}>Synthétiser mes goûts (${esc(CAT_LABEL[cat])})</button></div>`
          : `<div class="muted">Aucune dégustation notée pour ce type. Notez vos bouteilles depuis l'onglet Mon bar.</div>`}
        ${p ? `<div class="fiche">
          <div class="fname">Profil de goût — ${esc(CAT_LABEL[cat])} <span class="sub">(${esc(p.nb_bouteilles)} bouteilles)</span></div>
          ${p.resume ? `<p>${esc(p.resume)}</p>` : ""}
          <div class="cols2"><div>${ul("J'aime", p.aime)}</div><div>${ul("J'aime moins", p.aime_pas)}</div></div>
          <div class="cols2"><div>${ul("Styles préférés", p.styles_preferes)}</div><div>${ul("Origines préférées", p.origines_preferees)}</div></div>
          ${ul("Pistes d'achat", p.pistes_achat)}
          ${ul("À éviter", p.a_eviter)}
          <div class="sub">Utilisé automatiquement par « Vérifier l'opportunité » pour ce type.</div>
          <div class="actions"><button data-act="clear" data-kind="profil_${esc(cat)}">Effacer</button></div>
        </div>` : ""}`;
    }

    _renderPlan(d) {
      const { shelves, cols } = d.layout;
      const a = d.plan_analysis;
      const sel = this._form.place_spirit || "";
      let grid = "";
      for (let s = 1; s <= shelves; s++) {
        let cells = "";
        for (let c = 1; c <= cols; c++) {
          const key = `E${s}-${c}`;
          const sp = this._spirit(d.plan[key]);
          cells += `<button class="cell ${sp ? "full" : ""} ${this._cell === key ? "sel" : ""}" data-cell="${key}" title="${esc(sp ? sp.label : "Libre")}">
            <span class="ck">${key}</span><span class="cn">${sp ? esc(sp.label) : "Libre"}</span></button>`;
        }
        grid += `<div class="shelf"><div class="sh">Étagère ${s}</div><div class="cells" style="grid-template-columns:repeat(${cols},minmax(0,1fr))">${cells}</div></div>`;
      }
      const cellSp = this._cell ? this._spirit(d.plan[this._cell]) : null;
      const opts = a.non_placees.map((u) => [u.id, `${u.label} (reste ${u.restant})`]);
      return `
        <div class="stats">
          <div><span>Rangées</span><b>${esc(a.total_plan)} / ${esc(a.total_inventaire)}</b></div>
          <div><span>Cases libres</span><b>${esc(a.cases_libres)}</b></div>
          <div><span>Non placées</span><b>${esc(a.non_placees.reduce((t, u) => t + u.restant, 0))}</b></div>
        </div>
        ${a.suroccupees.length ? `<div class="warn">Plus de cases que de bouteilles : ${a.suroccupees.map((o) => esc(o.label)).join(", ")}</div>` : ""}
        <div class="grid">
          ${this._select("place_spirit", "Bouteille à ranger (puis cliquez une case)", opts)}
        </div>
        <div class="plan">${grid}</div>
        ${this._cell ? `<div class="fiche"><div class="fname">Case ${esc(this._cell)}</div>
          ${cellSp ? `<div>${esc(cellSp.label)}</div>` : `<div class="muted">Libre</div>`}
          <div class="actions">
            ${sel ? `<button class="primary" data-act="place">Ranger ici</button>` : ""}
            ${cellSp ? `<button data-act="clear-cell">Vider la case</button>` : ""}
          </div></div>` : ""}
        <div class="actions">
          <button data-act="autofill">⚡ Remplissage automatique</button>
          <button class="danger" data-act="empty">Vider tout le plan</button>
        </div>
        <details><summary>Dimensions du bar</summary>
          <div class="grid">
            ${this._input("etageres", `Étagères (actuel ${shelves})`, { type: "number", min: 1 })}
            ${this._input("colonnes", `Colonnes (actuel ${cols})`, { type: "number", min: 1 })}
          </div>
          <div class="actions"><button data-act="layout">Appliquer</button></div>
        </details>`;
    }

    _renderBarman(d) {
      const r = d.cocktail;
      const opts = d.spirits.map((s) => [s.id, s.label]);
      return `
        <div class="grid">
          ${this._select("c_spirit", "Bouteille (optionnel)", opts)}
          ${this._select("c_type", "Type d'alcool", TYPES.map((t) => [t, t]), false)}
          ${this._input("c_ingredients", "Ingrédients disponibles", { wide: true, ph: "citron vert, menthe, sirop…" })}
          ${this._input("c_occasion", "Occasion / envie", { wide: true, ph: "apéro d'été, digestif…" })}
        </div>
        <div class="actions"><button class="primary" data-act="cocktail" ${d.status === "en_cours" ? "disabled" : ""}>🍸 Lancer la suggestion</button></div>
        ${r ? `<div class="fiche">
          <div class="fname">${esc(r.cocktail)} <span class="chip">${esc(r.niveau)}</span></div>
          <div class="sub">${esc([r.style, r.base, r.bouteille].filter(Boolean).join(" · "))}</div>
          ${r.raison ? `<p>${esc(r.raison)}</p>` : ""}
          <div class="cols2">
            <div><h4>Ingrédients</h4><ul>${r.ingredients.map((i) => `<li>${esc(i)}</li>`).join("")}</ul></div>
            <div><h4>Recette</h4><ol>${r.recette.map((i) => `<li>${esc(i)}</li>`).join("")}</ol></div>
          </div>
          <div class="sub">🥂 ${esc(r.verre || "-")} · 🍋 ${esc(r.garnish || "-")}</div>
          ${r.alternatives.length ? `<div class="sub">Variantes : ${r.alternatives.map(esc).join(" · ")}</div>` : ""}
          <div class="actions"><button data-act="clear" data-kind="cocktail">Effacer</button></div>
        </div>` : ""}`;
    }

    _renderAdvice(d) {
      const a = d.audit;
      const p = d.purchase;
      const ul = (t, arr) => (arr?.length ? `<h4>${esc(t)}</h4><ul>${arr.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "");
      const v = p ? VERDICT[p.verdict] || ["", p.verdict] : null;
      return `
        ${this._renderProfile(d)}
        <h3>📊 Stratégie du bar</h3>
        <div class="actions"><button class="primary" data-act="audit" ${d.status === "en_cours" ? "disabled" : ""}>Analyser mon bar</button></div>
        ${a ? `<div class="fiche">
          ${a.score !== null ? `<div class="fname">Score ${esc(a.score)}/10</div>` : ""}
          ${a.resume ? `<p>${esc(a.resume)}</p>` : ""}
          <div class="cols2"><div>${ul("Forces", a.forces)}</div><div>${ul("Manques", a.manques)}</div></div>
          ${ul("Achats recommandés", a.achats_recommandes)}
          ${ul("Cocktails réalisables", a.cocktails_realisables)}
          <div class="actions"><button data-act="clear" data-kind="audit">Effacer</button></div>
        </div>` : ""}
        <h3>🛒 Opportunité d'achat</h3>
        <div class="grid">
          ${this._input("p_nom", "Bouteille envisagée", { wide: true })}
          ${this._input("p_type", "Type")}
          ${this._input("p_prix", "Prix (€)", { type: "number", step: 0.5 })}
          ${this._input("p_budget", "Budget max (€)", { type: "number", step: 5 })}
        </div>
        <div class="actions"><button class="primary" data-act="purchase" ${d.status === "en_cours" ? "disabled" : ""}>Vérifier l'opportunité</button></div>
        ${p ? `<div class="fiche">
          <div class="fname">${esc(v[0])} ${esc(v[1])} — ${esc(p.bouteille)}${p.score !== null ? ` <span class="chip">${esc(p.score)}/10</span>` : ""}</div>
          ${p.raison ? `<p>${esc(p.raison)}</p>` : ""}
          ${p.adequation_gouts && p.adequation_gouts !== "-" ? `<p>🍷 <b>Vos goûts :</b> ${esc(p.adequation_gouts)}</p>` : ""}
          <table>
            ${p.prix_estime ? `<tr><th>Prix estimé</th><td>${esc(p.prix_estime)}</td></tr>` : ""}
            ${p.doublon && p.doublon !== "-" ? `<tr><th>Doublon</th><td>${esc(p.doublon)}</td></tr>` : ""}
            ${p.alternative ? `<tr><th>Alternative</th><td>${esc(p.alternative)}</td></tr>` : ""}
          </table>
          ${ul("Cocktails débloqués", p.cocktails_debloques)}
          <div class="actions"><button data-act="clear" data-kind="purchase">Effacer</button></div>
        </div>` : ""}`;
    }

    // ----------------------------------------------------------- événements
    _bind() {
      const root = this.shadowRoot;
      root.querySelectorAll("[data-tab]").forEach((b) =>
        b.addEventListener("click", () => {
          this._tab = b.dataset.tab;
          try { localStorage.setItem("bar-ia-tab", this._tab); } catch (_e) { /* ignoré */ }
          this._error = "";
          this._render();
        })
      );
      root.querySelectorAll("[data-f]").forEach((el) => {
        const ev = el.tagName === "SELECT" || el.type === "checkbox" ? "change" : "input";
        el.addEventListener(ev, () => {
          this._form[el.dataset.f] = el.type === "checkbox" ? el.checked : el.value;
          if (ev === "change") this._render();
        });
      });
      root.querySelectorAll("[data-sel]").forEach((el) =>
        el.addEventListener("click", (e) => {
          if (e.target.closest("button")) return;
          this._selected = this._selected === el.dataset.sel ? null : el.dataset.sel;
          this._editing = null;
          this._render();
        })
      );
      root.querySelectorAll("[data-adj]").forEach((b) =>
        b.addEventListener("click", () =>
          this._call("adjust_stock", { spirit_id: b.dataset.adj, delta: Number(b.dataset.delta) })
        )
      );
      root.querySelectorAll("[data-del]").forEach((b) =>
        b.addEventListener("click", () => {
          const s = this._spirit(b.dataset.del);
          if (s && confirm(`Supprimer « ${s.label} » du bar ?`)) {
            this._selected = null;
            this._call("delete_spirit", { spirit_id: s.id });
          }
        })
      );
      root.querySelectorAll("[data-cell]").forEach((b) =>
        b.addEventListener("click", () => {
          this._cell = this._cell === b.dataset.cell ? null : b.dataset.cell;
          this._render();
        })
      );
      root.querySelectorAll("[data-upload]").forEach((inp) =>
        inp.addEventListener("change", () => this._uploadPhoto(inp.dataset.upload, inp.files?.[0]))
      );
      root.querySelectorAll("[data-photosearch]").forEach((inp) =>
        inp.addEventListener("change", () => this._photoSearch(inp.files?.[0]))
      );
      root.querySelectorAll("[data-deltaste]").forEach((b) =>
        b.addEventListener("click", () => {
          if (confirm("Supprimer cette dégustation ?"))
            this._call("delete_tasting", { spirit_id: b.dataset.sid, degustation_id: b.dataset.deltaste });
        })
      );
      root.querySelectorAll("[data-act]").forEach((b) =>
        b.addEventListener("click", () => this._action(b.dataset.act, b.dataset))
      );
    }

    async _ws(msg) {
      this._error = "";
      this._busy = true;
      this._render();
      try {
        await this._hass.callWS(msg);
      } catch (err) {
        this._error = err?.message || String(err);
      } finally {
        this._busy = false;
        this._render();
      }
    }

    async _uploadPhoto(sid, file) {
      if (!file) return;
      let data;
      try { data = await resizeImage(file, 800, 0.85); } catch (err) { this._error = err.message; return this._render(); }
      return this._ws({ type: "bar_ia/upload_image", spirit_id: sid, data });
    }

    async _photoSearch(file) {
      if (!file) return;
      let data;
      try { data = await resizeImage(file, 1600, 0.88); } catch (err) { this._error = err.message; return this._render(); }
      return this._ws({
        type: "bar_ia/search_photo", data,
        nom: this._txt("nom"), type_alcool: this._txt("type"), annee: this._txt("annee"), pays: this._txt("pays"),
      });
    }

    _num(name) {
      const n = parseFloat(String(this._form[name] ?? "").replace(",", "."));
      return Number.isFinite(n) ? n : undefined;
    }

    _txt(name) {
      return String(this._form[name] ?? "").trim();
    }

    _action(act, ds) {
      const f = this._form;
      switch (act) {
        case "search": {
          if (!this._txt("nom")) { this._error = "Indiquez le nom du spiritueux."; return this._render(); }
          const data = { nom: this._txt("nom"), type: this._txt("type"), annee: this._txt("annee"), pays: this._txt("pays"), maison: !!f.maison };
          if (f.maison) {
            data.base_utilisee = this._txt("base_utilisee");
            data.ingredients = this._txt("ingredients");
            for (const k of ["abv_base", "volume_base_ml", "volume_eau_ml", "sucre_g", "coef_sucre_ml_par_g", "prix_base_litre", "volume_bouteille_ml"]) {
              const n = this._num(k);
              if (n !== undefined) data[k] = n;
            }
          }
          return this._call("search", data);
        }
        case "add":
          return this._call("add_to_inventory", { quantite: Math.max(1, Math.round(this._num("quantite") || 1)) });
        case "clear":
          return this._call("clear_result", { resultat: ds.kind });
        case "place":
          if (this._cell && f.place_spirit) {
            const spirit = f.place_spirit;
            return this._call("place", { case: this._cell, spirit_id: spirit });
          }
          return;
        case "clear-cell":
          return this._call("clear_cell", { case: this._cell });
        case "autofill":
          if (confirm("Remplacer le plan actuel par un rangement automatique ?")) return this._call("autofill");
          return;
        case "empty":
          if (confirm("Vider toutes les cases du plan ?")) { this._cell = null; return this._call("empty_plan"); }
          return;
        case "layout": {
          const s = Math.round(this._num("etageres") || this._data.layout.shelves);
          const c = Math.round(this._num("colonnes") || this._data.layout.cols);
          this._cell = null;
          return this._call("set_layout", { etageres: s, colonnes: c });
        }
        case "cocktail": {
          const data = { type_alcool: f.c_type || "Aucun", ingredients: this._txt("c_ingredients"), occasion: this._txt("c_occasion") };
          if (f.c_spirit) data.spirit_id = f.c_spirit;
          return this._call("cocktail", data);
        }
        case "audit":
          return this._call("audit");
        case "rm-image":
          if (confirm("Retirer l'image de cette bouteille ?")) return this._call("remove_image", { spirit_id: ds.sid });
          return;
        case "url-image": {
          const url = this._txt(`u_${ds.sid}`);
          if (!/^https:\/\/\S+$/i.test(url)) { this._error = "Collez une adresse commençant par https://"; return this._render(); }
          return this._call("set_image_url", { spirit_id: ds.sid, url }).then(() => {
            if (!this._error) delete this._form[`u_${ds.sid}`];
            this._render();
          });
        }
        case "edit": {
          const sp = this._spirit(ds.sid);
          if (!sp) return;
          for (const [k] of EDIT_FIELDS) {
            const v = val(sp.fiche[k]);
            this._form[`e_${k}`] = v === undefined || v === null || v === "-" ? "" : String(v);
          }
          this._editing = sp.id;
          return this._render();
        }
        case "cancel-edit":
          this._editing = null;
          return this._render();
        case "save-edit": {
          const sp = this._spirit(ds.sid);
          if (!sp) return;
          if (!this._txt("e_nom")) { this._error = "Le nom ne peut pas être vide."; return this._render(); }
          const data = { spirit_id: sp.id };
          for (const [k] of EDIT_FIELDS) {
            const nv = this._txt(`e_${k}`) || "-";
            const ov = val(sp.fiche[k]);
            if (String(ov ?? "-") !== nv) data[k] = nv;
          }
          if (Object.keys(data).length === 1) { this._editing = null; return this._render(); }
          return this._call("update_spirit", data).then(() => {
            if (!this._error) this._editing = null;
            this._render();
          });
        }
        case "taste": {
          const data = { spirit_id: ds.sid, commentaire: this._txt("t_commentaire") };
          let any = false;
          for (const [k] of CRITERIA) {
            const n = this._num(`t_${k}`);
            if (n !== undefined) {
              if (n < 0 || n > 10) { this._error = "Les notes vont de 0 à 10."; return this._render(); }
              data[k] = n;
              any = true;
            }
          }
          if (!any) { this._error = "Notez au moins un critère (0 à 10)."; return this._render(); }
          if (this._txt("t_date")) data.date = this._txt("t_date");
          return this._call("add_tasting", data).then(() => {
            if (!this._error) for (const k of ["t_nez", "t_bouche", "t_finale", "t_qualite_prix", "t_commentaire", "t_date"]) delete this._form[k];
            this._render();
          });
        }
        case "profile":
          return this._call("taste_profile", { categorie: f.prof_cat || "rhum" });
        case "purchase": {
          if (!this._txt("p_nom")) { this._error = "Indiquez la bouteille envisagée."; return this._render(); }
          const data = { nom: this._txt("p_nom"), type: this._txt("p_type") };
          const prix = this._num("p_prix");
          const budget = this._num("p_budget");
          if (prix !== undefined) data.prix = prix;
          if (budget !== undefined) data.budget_max = budget;
          return this._call("purchase_check", data);
        }
      }
    }
  }

  const STYLE = `
    :host { display:block; }
    ha-card { padding: 12px 16px 16px; height: 100%; box-sizing: border-box; }
    .head { display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap; }
    .title { font-size: 1.3em; font-weight: 600; }
    .kpi { color: var(--secondary-text-color, #727272); }
    nav { display:flex; gap:4px; margin:10px 0; overflow-x:auto; }
    .tab { background:none; border:0; border-bottom:2px solid transparent; padding:6px 8px; cursor:pointer; color:var(--secondary-text-color, #727272); white-space:nowrap; font:inherit; }
    .tab.on { color: var(--primary-color, #03a9f4); border-color: var(--primary-color, #03a9f4); font-weight:600; }
    .body.busy { opacity:.6; pointer-events:none; }
    .grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap:8px; margin:8px 0; }
    .f { display:flex; flex-direction:column; gap:2px; font-size:.85em; color:var(--secondary-text-color, #727272); }
    .f.wide, .chk.wide { grid-column: 1 / -1; }
    .f input, .f select { font: inherit; font-size: 1rem; padding:6px 8px; border-radius:6px; border:1px solid var(--divider-color, #e0e0e0); background: var(--card-background-color, #fff); color: var(--primary-text-color, #212121); min-width:0; }
    .chk { display:flex; align-items:center; gap:6px; }
    .actions { display:flex; gap:8px; flex-wrap:wrap; align-items:flex-end; margin:8px 0; }
    button { font: inherit; cursor:pointer; border-radius:6px; border:1px solid var(--divider-color, #e0e0e0); background: var(--secondary-background-color, #f2f2f2); color: var(--primary-text-color, #212121); padding:6px 12px; }
    button.primary { background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); border-color: transparent; }
    button.danger { color: var(--error-color, #db4437); }
    button:disabled { opacity:.5; cursor:not-allowed; }
    .ic { width:30px; height:30px; padding:0; font-size:1.1em; }
    .status { padding:6px 10px; border-radius:6px; margin-bottom:8px; display:flex; align-items:center; gap:8px; }
    .status.run { background: rgba(var(--rgb-primary-color, 3,169,244), .12); }
    .status.ko, .err { background: rgba(219,68,55,.12); color: var(--error-color, #db4437); padding:6px 10px; border-radius:6px; margin-bottom:8px; }
    .warn { background: rgba(255,152,0,.15); padding:6px 10px; border-radius:6px; margin:6px 0; }
    .muted, .sub { color: var(--secondary-text-color, #727272); font-size:.9em; }
    .spin { width:14px; height:14px; border:2px solid currentColor; border-right-color:transparent; border-radius:50%; animation: s 0.8s linear infinite; }
    @keyframes s { to { transform: rotate(360deg); } }
    .fiche { border:1px solid var(--divider-color, #e0e0e0); border-radius:8px; padding:10px; margin:8px 0; }
    .fname { font-weight:600; font-size:1.1em; margin-bottom:4px; }
    table { width:100%; border-collapse:collapse; margin-top:6px; }
    th { text-align:left; vertical-align:top; color:var(--secondary-text-color, #727272); font-weight:500; padding:3px 8px 3px 0; width:35%; }
    td { padding:3px 0; word-break: break-word; }
    .stats { display:grid; grid-template-columns: repeat(auto-fill, minmax(120px,1fr)); gap:8px; margin-bottom:8px; }
    .stats div { background: var(--secondary-background-color, #f2f2f2); border-radius:8px; padding:6px 8px; display:flex; flex-direction:column; }
    .stats span { font-size:.8em; color:var(--secondary-text-color, #727272); }
    .chips { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:8px; }
    .chip { background: var(--secondary-background-color, #f2f2f2); border-radius:12px; padding:2px 8px; font-size:.85em; font-weight:400; }
    .list .row { display:flex; justify-content:space-between; align-items:center; gap:8px; padding:6px 4px; border-bottom:1px solid var(--divider-color, #e0e0e0); cursor:pointer; }
    .list .row.sel { background: var(--secondary-background-color, #f2f2f2); }
    .lbl { min-width:0; overflow-wrap:anywhere; }
    .qty { display:flex; align-items:center; gap:8px; flex-shrink:0; }
    .plan .shelf { margin-bottom:8px; }
    .sh { font-size:.8em; color:var(--secondary-text-color, #727272); margin-bottom:2px; }
    .cells { display:grid; gap:4px; }
    .cell { display:flex; flex-direction:column; align-items:flex-start; text-align:left; padding:4px 6px; min-height:48px; min-width:0; }
    .cell.full { background: rgba(var(--rgb-primary-color, 3,169,244), .15); }
    .cell.sel { outline:2px solid var(--primary-color, #03a9f4); }
    .ck { font-size:.7em; color:var(--secondary-text-color, #727272); }
    .cn { font-size:.8em; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:100%; }
    .cols2 { display:grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr)); gap:8px; }
    h3 { margin:12px 0 4px; font-size:1.05em; }
    h4 { margin:6px 0 2px; font-size:.95em; }
    ul, ol { margin:0; padding-left:20px; }
    details { margin-top:8px; }
    .thumb { width:44px; height:44px; border-radius:6px; object-fit:contain; background: var(--secondary-background-color, #f2f2f2); flex-shrink:0; }
    .thumb.ph { display:flex; align-items:center; justify-content:center; font-size:20px; opacity:.5; }
    .list .row { justify-content:flex-start; }
    .list .row .lbl { flex:1; }
    img.big { display:block; max-width:100%; max-height:280px; margin:8px auto; border-radius:8px; object-fit:contain; }
    div.big.ph { display:none; }
    .imgblock { border-bottom:1px solid var(--divider-color, #e0e0e0); padding-bottom:6px; }
    label.btn { font: inherit; cursor:pointer; border-radius:6px; border:1px solid var(--divider-color, #e0e0e0); background: var(--secondary-background-color, #f2f2f2); color: var(--primary-text-color, #212121); padding:6px 12px; display:inline-block; }
    label.btn.primary { background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); border-color: transparent; }
    label.btn.off { opacity:.5; pointer-events:none; }
    .note { color: #c77c02; font-weight:600; }
    .trow { display:flex; justify-content:space-between; gap:8px; padding:6px 0; border-bottom:1px solid var(--divider-color, #e0e0e0); }
    .f textarea { font: inherit; padding:6px 8px; border-radius:6px; border:1px solid var(--divider-color, #e0e0e0); background: var(--card-background-color, #fff); color: var(--primary-text-color, #212121); resize: vertical; }
    table.rank th { width:auto; }
    summary { cursor:pointer; color:var(--secondary-text-color, #727272); }
  `;

  // ------------------------------------------------------- éditeur visuel
  class BarIACardEditor extends HTMLElement {
    setConfig(config) {
      this._config = { ...(config || {}) };
      this._render();
    }

    set hass(_hass) { /* inutile pour l'éditeur */ }

    _emit(config) {
      this._config = config;
      this.dispatchEvent(new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true }));
    }

    _render() {
      if (!this.shadowRoot) this.attachShadow({ mode: "open" });
      const c = this._config || {};
      const hidden = Array.isArray(c.hidden_tabs) ? c.hidden_tabs : [];
      this.shadowRoot.innerHTML = `
        <style>
          :host { display:block; }
          .row { display:flex; flex-direction:column; gap:4px; margin-bottom:14px; }
          .lbl { font-weight:500; }
          .help { font-size:.85em; color: var(--secondary-text-color, #727272); }
          input[type=text], select { font: inherit; padding:8px; border-radius:6px; border:1px solid var(--divider-color, #ccc); background: var(--card-background-color, #fff); color: var(--primary-text-color, #212121); }
          .chk { display:flex; align-items:center; gap:8px; padding:2px 0; }
        </style>
        <div class="row">
          <span class="lbl">Titre</span>
          <input type="text" id="title" value="${esc(c.title ?? "")}" placeholder="🥃 Bar IA">
        </div>
        <div class="row">
          <span class="lbl">Onglets affichés</span>
          ${TABS.map(([k, l]) => `<label class="chk"><input type="checkbox" data-tab="${k}" ${hidden.includes(k) ? "" : "checked"}> ${l}</label>`).join("")}
          <span class="help">Décochez un onglet pour le masquer. Au moins un onglet reste affiché.</span>
        </div>
        <div class="row">
          <span class="lbl">Onglet affiché à l'ouverture</span>
          <select id="default_tab">
            <option value="">Dernier onglet utilisé</option>
            ${TABS.filter(([k]) => !hidden.includes(k)).map(([k, l]) => `<option value="${k}" ${c.default_tab === k ? "selected" : ""}>${l}</option>`).join("")}
          </select>
        </div>`;
      const R = this.shadowRoot;
      R.getElementById("title").addEventListener("change", (e) => {
        const cfg = { ...this._config };
        if (e.target.value.trim()) cfg.title = e.target.value; else delete cfg.title;
        this._emit(cfg);
      });
      R.querySelectorAll("[data-tab]").forEach((el) =>
        el.addEventListener("change", () => {
          const hide = [...R.querySelectorAll("[data-tab]")].filter((x) => !x.checked).map((x) => x.dataset.tab);
          if (hide.length >= TABS.length) { el.checked = true; return; }
          const cfg = { ...this._config };
          if (hide.length) cfg.hidden_tabs = hide; else delete cfg.hidden_tabs;
          if (cfg.default_tab && hide.includes(cfg.default_tab)) delete cfg.default_tab;
          this._emit(cfg);
          this._render();
        })
      );
      R.getElementById("default_tab").addEventListener("change", (e) => {
        const cfg = { ...this._config };
        if (e.target.value) cfg.default_tab = e.target.value; else delete cfg.default_tab;
        this._emit(cfg);
      });
    }
  }

  if (!customElements.get("bar-ia-card-editor")) customElements.define("bar-ia-card-editor", BarIACardEditor);
  customElements.define("bar-ia-card", BarIACard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "bar-ia-card",
    name: "Bar IA",
    description: "Gestionnaire de bar piloté par IA : recherche, stock, plan, barman, conseils.",
    preview: false,
  });
  console.info(`%c BAR-IA-CARD %c ${VERSION} `, "background:#6d4c41;color:#fff", "background:#eee;color:#333");
})();
