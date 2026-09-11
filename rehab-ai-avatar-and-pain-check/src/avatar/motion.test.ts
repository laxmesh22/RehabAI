import { cycleAt, timingFor, staircase, elevationDir, sagittalIK, wallHeightFor, clampTarget, cycleLength } from './motion'
const near = (a: number, b: number, tol = 1e-6) => { if (Math.abs(a - b) > tol) throw new Error(`${a} != ${b}`) }
const t = timingFor(45, 18, 5, 1.5)
near(t.moveS, 2.5)
near(cycleAt(0, t).progress, 0)
near(cycleAt(t.moveS / 2, t).progress, 0.5)
if (cycleAt(t.moveS + 1, t).phase !== 'hold') throw new Error('hold')
near(cycleAt(t.moveS + t.holdS + t.returnS, t).progress, 0)
near(cycleAt(cycleLength(t) + 0.01, t).rep, 1)
near(staircase(1, 6), 1); near(staircase(0, 6), 0); near(staircase(0.5, 6), 0.5)
let d = elevationDir(0, 0, 1); near(d[0], 0); near(d[1], -1); near(d[2], 0)
d = elevationDir(90, 90, 1); near(d[1], 0, 1e-9); near(d[2], 1)
d = elevationDir(90, 0, -1); near(d[0], -1); near(d[1], 0, 1e-9)
let ik = sagittalIK(0.57, 0, 0.29, 0.28)            // straight arm forward at shoulder height
near(ik.elevation, 90, 2); near(ik.elbowFlex, 0, 3)
ik = sagittalIK(0.40, -0.2, 0.29, 0.28)             // hand low on the wall: elbow bends
if (!(ik.elbowFlex > 20 && ik.elevation < 60)) throw new Error('bent elbow ' + JSON.stringify(ik))
const w = wallHeightFor(100, 0.40, 0.29, 0.28); near(w.elevation, 100, 0.01)
const wmax = wallHeightFor(170, 0.40, 0.29, 0.28)
if (wmax.elevation > 140) throw new Error('cap ' + wmax.elevation)
near(clampTarget('er', 200), 90); near(clampTarget('pendulum', 50), 0)
console.log('motion tests passed; wall max elevation', wmax.elevation.toFixed(1), 'wall 100deg height', w.height.toFixed(3))
