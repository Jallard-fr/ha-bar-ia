# 🥃 Bar IA pour Home Assistant

Un gestionnaire de bar intelligent pour Home Assistant, piloté par l'IA (Google Gemini, OpenAI… via **AI Task**).

Identifiez vos spiritueux, générez des fiches techniques, gérez votre stock et le plan de rangement, recevez des suggestions de cocktails, une analyse de votre bar et des avis d'achat.

> Projet inspiré du travail d'**[Aldoushx](https://github.com/aldoushx/ha-cellier-ia/)** (Home Assistant Wine Cellar Manager).

---

## ✨ Nouveautés de la version 2

La version 2 est une **intégration Home Assistant** qui s'installe en quelques clics :

| Avant (package YAML) | Maintenant (intégration) |
| :--- | :--- |
| Copier 8 fichiers dans `/config`, `packages/`, `python_scripts/` | Installation via HACS |
| Modifier `configuration.yaml` | Rien à modifier |
| Créer un jeton d'accès longue durée et le coller dans 5 scripts | **Aucun jeton** |
| Installer `config-template-card` et `layout-card` | Carte fournie et chargée automatiquement |
| Inventaire stocké dans l'attribut d'un capteur (risque de perte) | Stockage persistant dans `.storage` |
| Plan HTML public sur `/local/bar_plan.html` | Plan affiché dans la carte, derrière l'authentification HA |
| Scripts lancés via `shell_command` toutes les 10 s | Services natifs, aucune interrogation périodique |

---

## 🛠 Prérequis

1. **Home Assistant 2025.8 ou plus récent**.
2. **[HACS](https://hacs.xyz/)** installé.
3. Une intégration fournissant une entité **Tâche IA** (`ai_task.*`), par exemple :
   - *Google Generative AI* (clé API à créer sur [Google AI Studio](https://aistudio.google.com/)) ;
   - ou OpenAI, Anthropic, Ollama…

---

## 🚀 Installation

### 1. Ajouter le dépôt dans HACS

1. HACS → menu ⋮ → **Dépôts personnalisés**.
2. URL : `https://github.com/jallard-fr/ha-bar-ia`, catégorie **Intégration**.
3. Recherchez **Bar IA**, cliquez sur **Télécharger**, puis **redémarrez Home Assistant**.

### 2. Ajouter l'intégration

**Paramètres › Appareils et services › Ajouter une intégration › Bar IA**

Choisissez l'entité IA à utiliser. Si vous laissez vide, Bar IA utilise l'entité préférée définie dans *Paramètres › Système › IA*. Vous pourrez la changer plus tard via **Configurer**.

### 3. Ajouter la carte

Dans un tableau de bord : **Modifier › Ajouter une carte › Bar IA**, ou en YAML :

```yaml
type: custom:bar-ia-card
```

Options facultatives (aussi réglables dans l'éditeur visuel de la carte) :

```yaml
type: custom:bar-ia-card
title: "🥃 Mon bar"
default_tab: bar          # recherche | bar | plan | barman | conseils
hidden_tabs:              # onglets à masquer
  - plan
  - barman
```

**Pleine largeur** : dans une vue « Sections », la carte occupe toute la largeur de sa section. Pour qu'elle remplisse tout l'écran, placez-la seule dans une vue de type **Panneau** (Modifier la vue › Type de vue › Panneau).

C'est tout : aucune ressource à déclarer, aucun fichier à copier.

---

## 📖 Utilisation (onglets de la carte)

| Onglet | Fonction |
| :--- | :--- |
| 🔍 **Recherche** | **📷 Rechercher par photo** : photographiez l'étiquette, l'IA identifie la bouteille et la photo devient son image. Ou saisissez nom, type et **pays d'origine** : l'IA trouve aussi **automatiquement l'image** de la bouteille (optionnel, aide l'IA à trouver la bonne bouteille) → fiche générée par l'IA (origine, distillerie, ABV, prix, profil aromatique, corps cocktail, cocktails conseillés). Cochez **Bouteille maison** pour calculer sans IA l'ABV, le volume, le coût, le prix conseillé et la marge. Puis **Ajouter au bar**. |
| 🥃 **Mon bar** | Stock (+1 / −1), fiche détaillée, suppression, valorisation totale, maison/industriel, répartition par type. Vignette de chaque bouteille et bouton **🔎 Trouver les images manquantes** pour les bouteilles existantes. Pour chaque bouteille : **image** (🖼️ **Google Images** : la recherche s'ouvre pré-remplie, vous copiez l'adresse de l'image choisie et la collez avec 📋 ; 🔎 recherche automatique ; photo prise avec l'appareil ou la galerie), **✏️ modification des informations** (prix, origine, distillerie, ABV…) et **dégustations datées** notées sur 10 (Nez, Bouche, Finale, Rapport qualité/prix) avec commentaire ; la note affichée est la moyenne. |
| 🗺️ **Plan** | Grille étagères × colonnes. Choisissez une bouteille puis cliquez une case pour la ranger. Remplissage automatique, vidage, changement de dimensions. |
| 🍸 **Barman** | Cocktail proposé selon une bouteille, le type d'alcool, vos ingrédients et l'occasion. |
| 📈 **Conseils** | **Mes dégustations** : classement par type et **synthèse IA de vos goûts** (ce que vous aimez, styles et origines préférés, pistes d'achat). **Analyse stratégique** du bar (score, forces, manques, achats recommandés) et **vérification d'une opportunité d'achat** (verdict, adéquation à vos goûts d'après vos dégustations et votre profil, doublons, cocktails débloqués, respect du budget). |

### Images via Google Images

Google ne propose plus d'API d'images ouverte aux nouveaux utilisateurs (Custom Search JSON API fermée en 2026, arrêtée le 1er janvier 2027). Bar IA ouvre donc Google Images avec la bonne recherche ; vous choisissez l'image, copiez son adresse (appui long › « Copier l'adresse de l'image ») et la collez dans la fiche. L'image est **téléchargée et stockée dans Home Assistant** (adresses https ou images `data:` copiées depuis les vignettes) : elle ne dépend plus du site d'origine.

### Recherche automatique d'images (en complément)

1. **Candidats** : [Open Food Facts](https://world.openfoodfacts.org) (base produits libre), [Wikimedia Commons](https://commons.wikimedia.org), puis adresses proposées par l'IA.
2. **Téléchargement contrôlé** : https uniquement, hôtes du réseau local refusés, 6 Mo maximum, conversion en JPEG 800 px.
3. **Vérification visuelle** (tolérante : même marque et même gamme suffisent) : 3 images au plus par bouteille. La source est affichée sous l'image.

Chaque bouteille demande 1 à 6 appels à l'IA. Le traitement des bouteilles existantes se fait une par une (pause de 2 s) et peut être arrêté à tout moment. Les bouteilles maison et celles qui ont déjà une image ne sont pas modifiées.

### Entités créées

Capteurs : bouteilles, valeur du bar, bouteilles et valeur maison / industrielles, nombre par type (rhum, whisky, gin, vodka, tequila/mezcal, liqueurs, vin, autres), bouteilles rangées, statut IA (`inactif` / `en_cours` / `erreur` avec la dernière erreur).

### Services (pour vos automatisations)

`bar_ia.search`, `add_to_inventory`, `adjust_stock`, `set_stock`, `update_spirit`, `find_image`, `find_missing_images`, `stop_job`, `set_image_url`, `remove_image`, `delete_spirit`, `cocktail`, `audit`, `purchase_check`, `clear_result`, `place`, `clear_cell`, `autofill`, `empty_plan`, `set_layout`, `add_tasting`, `delete_tasting`, `taste_profile`, `import_legacy`.

Les services IA renvoient leur résultat (`response_variable`) :

```yaml
action: bar_ia.cocktail
data:
  occasion: "apéritif d'été"
  ingredients: "citron vert, menthe"
response_variable: cocktail
```

---

## 🔁 Migration depuis la version 1 (package YAML)

1. Installez Bar IA (étapes ci-dessus) **sans supprimer l'ancien package**.
2. Au démarrage, l'intégration importe automatiquement l'inventaire de `sensor.bar_supersensor`, le plan `/config/bar_plan.json` et les dimensions (`input_number.bar_nb_etageres` et `input_number.bar_nb_colonnes`). Une notification confirme l'import.
3. Supprimez ensuite les anciens fichiers :
   - `packages/bar_cellier_ia_ultra.yaml`
   - `generate_bar_plan.py`, `bar_plan.json`, `www/bar_plan.html`
   - `python_scripts/analyze_bar_stock.py`, `bar_autofill.py`, `bar_empty.py`, `bar_ranger.py`, `bar_common.py`
   - les exclusions `recorder` qui les concernaient.
4. **Révoquez le jeton d'accès longue durée** utilisé par les anciens scripts : Profil › Sécurité › Jetons d'accès longue durée.

Pour relancer l'import manuellement : action `bar_ia.import_legacy` avec `force: true`.

---

## 🔐 Sécurité

- Aucun jeton : l'intégration s'exécute dans Home Assistant et n'appelle pas son API REST.
- Les photos sont réduites dans le navigateur, contrôlées (JPEG, 2 Mo maximum) et stockées dans `/config/bar_ia/images`. Elles ne sont servies qu'aux utilisateurs connectés (chemins signés). Les images web doivent être en `https://`.
- Les données du bar ne sont visibles que par les utilisateurs authentifiés (WebSocket HA). Aucun fichier public n'est généré dans `www/`.
- Les saisies sont validées (longueur, format des cases `E1-1`, bornes numériques).
- Le prompt envoyé à l'IA isole les saisies dans un bloc de données à ne jamais traiter comme des instructions.
- Les réponses de l'IA sont validées : seuls les champs attendus sont conservés et ils sont tronqués.
- La carte échappe toutes les valeurs affichées : une réponse IA contenant du HTML ne peut pas exécuter de script.

---

## 🧪 Développement

```bash
pip install -r requirements_test.txt
pytest
```

---

## 🙏 Remerciements

Un immense merci à [Aldoushx](https://github.com/aldoushx/ha-cellier-ia/) pour Home Assistant Wine Cellar Manager (AI Powered), dont l'architecture a directement inspiré ce projet.
