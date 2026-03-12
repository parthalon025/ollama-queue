/** @type {import('jest').Config} */
module.exports = {
    testEnvironment: 'node',
    transform: {
        '^.+\\.jsx?$': 'babel-jest',
    },
    // Mock preact + preact/hooks so JSX/hook imports resolve in CJS node env.
    // Also mock uplot to avoid canvas/DOM requirements in tests.
    moduleNameMapper: {
        '^preact$': '<rootDir>/src/__mocks__/preact.cjs',
        '^preact/hooks$': '<rootDir>/src/__mocks__/preact-hooks.cjs',
        '^@preact/signals$': '<rootDir>/src/__mocks__/preact-signals.cjs',
        '^uplot$': '<rootDir>/src/__mocks__/uplot.cjs',
        '^\\.\\./stores$': '<rootDir>/src/__mocks__/stores.cjs',
        '^\\.\\./stores/index\\.js$': '<rootDir>/src/__mocks__/stores.cjs',
        '^\\.\\./stores/queue\\.js$': '<rootDir>/src/__mocks__/stores.cjs',
        '^\\.\\./stores/health\\.js$': '<rootDir>/src/__mocks__/stores.cjs',
        '^\\.\\./stores/eval\\.js$': '<rootDir>/src/__mocks__/stores-eval.cjs',
        '^\\.\\./hooks/useActionFeedback\\.js$': '<rootDir>/src/__mocks__/useActionFeedback.cjs',
        '^\\.\\./utils/time\\.js$': '<rootDir>/src/__mocks__/utils-time.cjs',
        // Depth-2 patterns for components in src/components/<subdirectory>/ (e.g. eval/)
        '^\\.\\./\\.\\./stores$': '<rootDir>/src/__mocks__/stores.cjs',
        '^\\.\\./\\.\\./stores/index\\.js$': '<rootDir>/src/__mocks__/stores.cjs',
        '^\\.\\./\\.\\./stores/queue\\.js$': '<rootDir>/src/__mocks__/stores.cjs',
        '^\\.\\./\\.\\./stores/health\\.js$': '<rootDir>/src/__mocks__/stores.cjs',
        '^\\.\\./\\.\\./stores/eval\\.js$': '<rootDir>/src/__mocks__/stores-eval.cjs',
        '^\\.\\./\\.\\./hooks/useActionFeedback\\.js$': '<rootDir>/src/__mocks__/useActionFeedback.cjs',
        '^\\.\\./\\.\\./utils/time\\.js$': '<rootDir>/src/__mocks__/utils-time.cjs',
    },
    testMatch: ['**/*.test.js'],
};
