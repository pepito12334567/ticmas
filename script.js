const sections = document.querySelectorAll("section")
const navLinks = document.querySelectorAll(".nav-link")

function hideAllSections() {
    sections.forEach(section => {
        section.style.display = "none"
    })
}

function showSection(sectionId) {
    hideAllSections()
    document.querySelector(`#${sectionId}`).style.display = "block"
}

navLinks.forEach(link => {
    link.addEventListener("click", event => {
        event.preventDefault()
        const sectionId = link.getAttribute("href").substr(1)
        showSection(sectionId)
    })
})

// Mostrar solo la sección de contacto al cargar la página
showSection("contacto")