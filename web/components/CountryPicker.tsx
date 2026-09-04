import { COUNTRY_OPTIONS, MAX_FAVORITE_COUNTRIES, getScopeLabel, normalizeScopeId, scopeCountryCode, type ScopeId } from "../lib/areas";

interface Props {
  scope: ScopeId;
  favorites: readonly string[];
  ready: boolean;
  onScopeChange: (scope: ScopeId) => void;
  onToggleFavorite: (code: string) => void;
}

/** Native selection keeps keyboard/type-to-find behavior without a custom popup. */
export default function CountryPicker({ scope, favorites, ready, onScopeChange, onToggleFavorite }: Props) {
  const code = scopeCountryCode(scope);
  const favorite = Boolean(code && favorites.includes(code));
  const full = favorites.length >= MAX_FAVORITE_COUNTRIES;
  // Labels and collation come from the browser only after hydration.
  const options = ready ? [...COUNTRY_OPTIONS].sort((a, b) => a.label.localeCompare(b.label, "pl")) : [];
  const title = !code ? "Najpierw wybierz kraj" : full && !favorite ? "Usuń jeden ulubiony obszar, aby dodać kolejny" : `${favorite ? "Usuń z" : "Dodaj do"} ulubionych: ${getScopeLabel(scope)}`;
  return <section className="country-picker" aria-label="Kraj i ulubione obszary">
    <label htmlFor="scope-country">Wybierz kraj</label>
    <div className="country-choose-row">
      <select id="scope-country" value={ready ? code || "" : ""} disabled={!ready} aria-describedby="country-picker-note" onChange={event => {
        const next = normalizeScopeId(`country:${event.target.value}`);
        if (next) onScopeChange(next);
      }}>
        <option value="" disabled>Kraj lub terytorium…</option>
        {options.map(option => <option key={option.code} value={option.code}>{option.label}</option>)}
      </select>
      <button type="button" className="country-favorite-toggle" aria-label={title} title={title} aria-pressed={favorite} disabled={!ready || !code || (full && !favorite)} onClick={() => { if (code) onToggleFavorite(code); }}><span aria-hidden="true">{favorite ? "★" : "☆"}</span></button>
    </div>
    <p id="country-picker-note">Wybór filtruje podłączone źródła. Nie zwiększa ich zasięgu.</p>
    {favorites.length > 0 && <div className="favorite-areas">
      <p>Ulubione obszary <span>{favorites.length}/{MAX_FAVORITE_COUNTRIES}</span></p>
      <ul>{favorites.map(country => {
        const area = normalizeScopeId(`country:${country}`);
        if (!area) return null;
        const label = getScopeLabel(area);
        return <li key={country}>
          <button type="button" aria-label={`Obszar: ${label}`} aria-pressed={scope === area} onClick={() => onScopeChange(area)}><span>{label}</span></button>
          <button type="button" className="favorite-area-remove" aria-label={`Usuń z ulubionych: ${label}`} title={`Usuń z ulubionych: ${label}`} onClick={() => onToggleFavorite(country)}><span aria-hidden="true">×</span></button>
        </li>;
      })}</ul>
    </div>}
    <p className="favorite-areas-note">Ulubione tylko w tej przeglądarce.</p>
  </section>;
}
