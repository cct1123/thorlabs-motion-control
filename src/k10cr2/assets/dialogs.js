/* Dialog keyboard behavior only. Closing always discards unapplied edits. */
(() => {
    let active = null;
    let opener = null;
    function visible(element) {
        return !!element && element.getClientRects().length > 0;
    }
    function sync() {
        const dialog = [...document.querySelectorAll('.dialog')].find(visible) || null;
        if (dialog === active) return;
        if (!dialog) {
            const previous = opener;
            active = opener = null;
            if (visible(previous) && !previous.disabled) previous.focus({preventScroll: true});
            return;
        }
        opener = document.activeElement;
        active = dialog;
        const heading = dialog.querySelector('h2');
        if (heading) {
            heading.tabIndex = -1;
            heading.focus({preventScroll: true});
        }
    }
    document.addEventListener('keydown', event => {
        if (!active) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            active.querySelector('.close')?.click();
        } else if (event.key === 'Tab') {
            const controls = [...active.querySelectorAll(
                'button, input, select, textarea, a[href], [tabindex]'
            )].filter(element => !element.disabled && element.tabIndex >= 0 && visible(element));
            if (!controls.length) { event.preventDefault(); return; }
            const index = controls.indexOf(document.activeElement);
            if (index < 0 || (event.shiftKey && index === 0) ||
                    (!event.shiftKey && index === controls.length - 1)) {
                event.preventDefault();
                controls[event.shiftKey ? controls.length - 1 : 0].focus();
            }
        }
    });
    new MutationObserver(sync).observe(document.documentElement, {
        childList: true, subtree: true, attributes: true, attributeFilter: ['style']
    });
    sync();
})();
