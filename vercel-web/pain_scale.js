/** Pain 0–10 emoji check (ported from rehab-ai-avatar-and-pain-check painScale + PainSlider). */
(function (global) {
  const PAIN_ANCHORS = [
    { value: 0, label: 'No pain' },
    { value: 2, label: 'Mild' },
    { value: 4, label: 'Moderate' },
    { value: 6, label: 'Fairly severe' },
    { value: 8, label: 'Severe' },
    { value: 10, label: 'Worst pain' },
  ];
  const PAIN_MIN = 0;
  const PAIN_MAX = 10;
  const PAIN_EMOJI = ['😄', '🙂', '🙂', '😐', '😐', '😕', '😕', '🙁', '😣', '😖', '😭'];

  const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
  const snapPain = (value) => clamp(Math.round(value), PAIN_MIN, PAIN_MAX);

  function labelForPain(value) {
    const v = clamp(value, PAIN_MIN, PAIN_MAX);
    let best = PAIN_ANCHORS[0];
    let bestDist = Infinity;
    for (const a of PAIN_ANCHORS) {
      const d = Math.abs(a.value - v);
      if (d < bestDist) { bestDist = d; best = a; }
    }
    return best.label;
  }

  function tierForPain(value) {
    const v = snapPain(value);
    if (v <= 3) return 'low';
    if (v <= 6) return 'moderate';
    return 'high';
  }

  const emojiForPain = (value) => PAIN_EMOJI[snapPain(value)];

  function shouldFlag(current, previous) {
    const v = snapPain(current);
    if (v >= 8) return true;
    if (previous == null || previous === '') return false;
    return v - snapPain(previous) >= 2;
  }

  function flagMessage(moment) {
    return moment === 'after'
      ? 'That is more pain than before. Stop here for today and tell your physiotherapist.'
      : 'This is a lot of pain right now. Check the red-flag questions before starting.';
  }

  function renderPainCheck(host, opts) {
    const options = opts || {};
    const moment = options.moment || 'after';
    const previous = options.previousValue == null ? null : options.previousValue;
    const question = options.question || (moment === 'before'
      ? 'How much pain right now?'
      : 'How much pain during that exercise?');
    const qId = options.id || 'pain-check';
    let value = options.value == null ? null : snapPain(options.value);

    const paint = () => {
      const answered = value !== null;
      const flagged = answered && shouldFlag(value, previous);
      const buttons = [];
      for (let v = PAIN_MIN; v <= PAIN_MAX; v += 1) {
        const selected = value === v;
        buttons.push(`<button type="button" role="radio" aria-checked="${selected}"
          aria-label="${v} out of 10, ${labelForPain(v)}" data-pain-value="${v}" data-tier="${tierForPain(v)}"
          class="pain-slider__btn${selected ? ' is-selected' : ''}" tabindex="${(answered ? selected : v === 0) ? 0 : -1}">
          <span class="pain-slider__emoji" aria-hidden="true">${emojiForPain(v)}</span>
          <span class="pain-slider__num">${v}</span>
        </button>`);
      }
      host.innerHTML = `<div class="pain-slider" id="${qId}">
        <p class="pain-slider__question" id="${qId}-label">${question}</p>
        <div class="pain-slider__row" role="radiogroup" aria-labelledby="${qId}-label">${buttons.join('')}</div>
        <p class="pain-slider__scale-ends"><span>No pain</span><span>Worst pain</span></p>
        <p class="pain-slider__value-label">${answered ? `${value} — ${labelForPain(value)}` : 'Tap a face to answer'}</p>
        ${flagged ? `<p class="pain-slider__flag" role="status">${flagMessage(moment)}</p>` : ''}
      </div>`;
      host.querySelectorAll('[data-pain-value]').forEach((btn) => {
        btn.addEventListener('click', () => {
          value = snapPain(Number(btn.dataset.painValue));
          options.onChange?.(value);
          paint();
        });
      });
    };
    paint();
    return {
      get value() { return value; },
      setValue(v) { value = v == null ? null : snapPain(v); paint(); },
    };
  }

  global.RehabPain = {
    PAIN_ANCHORS, PAIN_MIN, PAIN_MAX, snapPain, labelForPain, tierForPain,
    emojiForPain, shouldFlag, flagMessage, renderPainCheck,
  };
})(window);
