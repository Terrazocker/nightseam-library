// Evaluate authored shared solar code directly, independently of Python's bridge.
import { solarSession } from '../../core/solar_sessions.js';
const inputs = JSON.parse(process.argv[2]);
console.log(JSON.stringify(inputs.map(([date, lat, lon]) =>
  solarSession(Date.parse(date.endsWith('Z') ? date : `${date}Z`), lat, lon))));
