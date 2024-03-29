// Define this if your assets are in a specific directory, otherwise, you might not need it.
const themeDir = './themes/atharva/';

module.exports = {
  plugins: [
    require('postcss-import'),
    require('tailwindcss'),
    require('autoprefixer'),
    require('@fullhuman/postcss-purgecss')({
      content: [
        './hugo_stats.json',
        "./assets/js/*.js",
        "./content/*.md",
        "./layouts/**/*.html",
        "./static/js/*.js",

      ],
      defaultExtractor: (content) => {
        const els = JSON.parse(content).htmlElements;
        return els.tags.concat(els.classes, els.ids);
      },
      safelist: ['dynamic-class', /regex-pattern/],
    })
  ]
};
