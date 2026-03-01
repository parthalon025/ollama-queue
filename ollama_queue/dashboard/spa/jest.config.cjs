/** @type {import('jest').Config} */
module.exports = {
    testEnvironment: 'node',
    transform: {
        '^.+\\.jsx?$': 'babel-jest',
    },
    // Mock preact so the import { h } from 'preact' in GanttChart.jsx resolves
    // without needing the full preact package available in CJS form.
    moduleNameMapper: {
        '^preact$': '<rootDir>/src/__mocks__/preact.cjs',
    },
    testMatch: ['**/*.test.js'],
};
