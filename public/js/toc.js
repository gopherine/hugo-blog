const details = document.querySelector("#toc-details");
const tocList = document.querySelector("#toc-list");

// Initializes TOC and sets up necessary event listeners
(() => {
    generateToc();
    details.addEventListener('click', toggleToc);
})();

// Toggles the TOC's display state and sets up or removes a body click listener
function toggleToc(event) {
    const isTocVisible = details.style.display === 'block';
    details.style.display = isTocVisible ? 'none' : 'block';

    // Stops the event from bubbling up to the body when TOC is clicked
    event.stopPropagation();

    // Sets up or removes the body click listener based on TOC's visibility
    document.body[isTocVisible ? 'removeEventListener' : 'addEventListener']('click', closeToc, true);
}

// Closes the TOC and removes the body click listener
function closeToc() {
    details.style.display = 'none';
    document.body.removeEventListener('click', closeToc, true);
}

// Generates the TOC based on headings in the content
function generateToc() {
    const headings = document.querySelector('.markdown-body').querySelectorAll('h1, h2, h3, h4, h5, h6');
    
    headings.forEach(heading => {
        const level = parseInt(heading.tagName.substring(1), 10); // Extracts number from heading tag (e.g., "2" from "H2")
        const item = document.createElement('a');
        item.className = "filter-item SelectMenu-item ws-normal wb-break-word line-clamp-2 py-1 toc-item";
        item.href = `#${heading.id}`;
        item.innerText = heading.textContent;
        item.style.paddingLeft = `${level * 12}px`;
        tocList.appendChild(item);
    });
}

// TODO: Implement highlight on scroll functionality
