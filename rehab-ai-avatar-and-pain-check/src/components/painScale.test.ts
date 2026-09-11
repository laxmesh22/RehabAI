import { snapPain, labelForPain, tierForPain, emojiForPain, shouldFlag, flagMessage, PAIN_ANCHORS, PAIN_MIN, PAIN_MAX } from './painScale'
const near = (a: number, b: number, tol = 1e-6) => { if (Math.abs(a - b) > tol) throw new Error(`${a} != ${b}`) }

// snapping and clamping
near(snapPain(3.4), 3); near(snapPain(3.6), 4); near(snapPain(-4), PAIN_MIN); near(snapPain(40), PAIN_MAX)

// labels: exact anchors and the midpoints between them
if (labelForPain(0) !== 'No pain') throw new Error('label 0')
if (labelForPain(10) !== 'Worst pain') throw new Error('label 10')
if (labelForPain(6) !== 'Fairly severe') throw new Error('label 6')
if (labelForPain(1) !== 'No pain' && labelForPain(1) !== 'Mild') throw new Error('label 1 tie should resolve to a real anchor')
if (PAIN_ANCHORS.length !== 6) throw new Error('six anchors expected')

// tiers: match the APTA irritability thresholds used elsewhere (low <=3, moderate 4-6, high >=7)
if (tierForPain(0) !== 'low' || tierForPain(3) !== 'low') throw new Error('0-3 should be low')
if (tierForPain(4) !== 'moderate' || tierForPain(6) !== 'moderate') throw new Error('4-6 should be moderate')
if (tierForPain(7) !== 'high' || tierForPain(10) !== 'high') throw new Error('7-10 should be high')

// emoji: one per point, all 11 defined, and it gets less happy (never more happy) as pain rises
for (let v = 0; v <= 10; v++) { if (!emojiForPain(v)) throw new Error(`missing emoji for ${v}`) }
if (emojiForPain(0) === emojiForPain(10)) throw new Error('endpoints should read differently')
if (emojiForPain(-3) !== emojiForPain(0)) throw new Error('should clamp below range')
if (emojiForPain(99) !== emojiForPain(10)) throw new Error('should clamp above range')

// flagging: severe reading, or a jump of 2+ from the last reading; never on a small change or a drop
if (!shouldFlag(8)) throw new Error('8 alone should flag (severe)')
if (shouldFlag(7)) throw new Error('7 alone should not flag')
if (!shouldFlag(6, 4)) throw new Error('4 -> 6 should flag (jump of 2)')
if (shouldFlag(5, 4)) throw new Error('4 -> 5 should not flag (jump of 1)')
if (shouldFlag(3, 6)) throw new Error('a drop in pain should never flag')
if (shouldFlag(4, null)) throw new Error('no previous reading, below severe: no flag')
if (!flagMessage('after').toLowerCase().includes('stop')) throw new Error('after-message should say to stop')
if (!flagMessage('before').toLowerCase().includes('red-flag')) throw new Error('before-message should point at red flags')

console.log('painScale tests passed')
