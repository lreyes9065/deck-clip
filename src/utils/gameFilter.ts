const GAME_FILTER_KEY = "deckclip.gameFilter";

export const loadGameFilter = () => {
  try { return window.localStorage.getItem(GAME_FILTER_KEY); }
  catch { return null; }
};

export const saveGameFilter = (value: string | null) => {
  try {
    if (value) window.localStorage.setItem(GAME_FILTER_KEY, value);
    else window.localStorage.removeItem(GAME_FILTER_KEY);
  } catch { /* DeckClip still works if Steam disables local storage. */ }
};
