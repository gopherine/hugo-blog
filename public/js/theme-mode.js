// Immediately-invoked function expression to set theme based on current preference
(() => {
  setTheme(currentTheme());
})();

// Toggles the website's theme between light and dark mode
function switchTheme() {
  const newStyle = currentTheme() === 'light' ? 'dark' : 'light';
  setTheme(newStyle);
  updateIcon(newStyle);
}

// Updates the website's theme and persists the choice in localStorage
function setTheme(style) {
  document.documentElement.setAttribute('data-color-mode', style);
  localStorage.setItem('data-color-mode', style);
  // Remove 'isInitialToggle' class from all elements that have it
  document.querySelectorAll('.isInitialToggle').forEach(elem => elem.classList.remove('isInitialToggle'));
}

// Determines the current theme by checking localStorage and system preference
function currentTheme() {
  return localStorage.getItem('data-color-mode') || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
}

// Updates the GitHub icon's attributes based on the theme
function updateIcon(style) {
  const iconElement = document.getElementById('github-icon');
  if (!iconElement) return;

  if (style === 'dark') {
    iconElement.setAttribute('class', 'octicon');
    iconElement.setAttribute('color', '#f0f6fc');
  } else {
    iconElement.removeAttribute('color');
    iconElement.removeAttribute('class');
  }
}
