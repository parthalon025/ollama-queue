// OnboardingOverlay.test.js
// Tests for the OnboardingOverlay component using the node-env vnode-tree approach.
// No DOM — components are called as plain functions, vnode tree is walked directly.
import { jest } from '@jest/globals';

// @preact/signals is mocked via moduleNameMapper → preact-signals.cjs, which exports useSignal
// as a jest.fn() so individual tests can call mockReturnValueOnce() for state-specific cases.

import _OnboardingOverlay from './OnboardingOverlay.jsx';
const OnboardingOverlay = _OnboardingOverlay.default || _OnboardingOverlay;
import { useSignal } from '@preact/signals';

// ---------------------------------------------------------------------------
// Vnode tree helpers
// ---------------------------------------------------------------------------

function findAll(vnode, predicate) {
    if (!vnode || typeof vnode !== 'object') return [];
    const results = predicate(vnode) ? [vnode] : [];
    const children = Array.isArray(vnode.props?.children)
        ? vnode.props.children
        : vnode.props?.children != null ? [vnode.props.children] : [];
    return results.concat(...children.map(c => findAll(c, predicate)));
}

function collectText(vnode) {
    if (typeof vnode === 'string' || typeof vnode === 'number') return [String(vnode)];
    if (!vnode || typeof vnode !== 'object') return [];
    const texts = [];
    const children = vnode.props?.children;
    if (Array.isArray(children)) {
        for (const c of children) texts.push(...collectText(c));
    } else if (children !== undefined && children !== null) {
        texts.push(...collectText(children));
    }
    return texts;
}

function findByText(vnode, text) {
    // Returns all nodes that contain the given text anywhere in their subtree
    return findAll(vnode, n => {
        const texts = collectText(n);
        return texts.some(t => t.includes(text));
    });
}

// ---------------------------------------------------------------------------
// localStorage mock — localStorage does not exist in node testEnvironment
// ---------------------------------------------------------------------------

beforeEach(() => {
    global.localStorage = {
        getItem: jest.fn(() => null),
        setItem: jest.fn(),
    };
});

afterEach(() => {
    delete global.localStorage;
    jest.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test('renders null when localStorage has oq_onboarding_done=1', () => {
    // When key is set, component should return null (no modal rendered)
    global.localStorage.getItem.mockReturnValue('1');
    const vnode = OnboardingOverlay({});
    expect(vnode).toBeNull();
});

test('renders step 1 content when localStorage is empty', () => {
    global.localStorage.getItem.mockReturnValue(null);
    const vnode = OnboardingOverlay({});
    expect(vnode).not.toBeNull();
    const texts = collectText(vnode);
    expect(texts.some(t => t.includes('Welcome to ollama-queue'))).toBe(true);
});

test("renders step indicator text 'Step 1 of 5'", () => {
    global.localStorage.getItem.mockReturnValue(null);
    const vnode = OnboardingOverlay({});
    const texts = collectText(vnode);
    expect(texts.some(t => t.includes('Step 1 of 5'))).toBe(true);
});

test('renders Next button on step 1', () => {
    global.localStorage.getItem.mockReturnValue(null);
    const vnode = OnboardingOverlay({});
    const buttons = findAll(vnode, n => n?.type === 'button');
    const nextBtn = buttons.find(b => collectText(b).some(t => t === 'Next'));
    expect(nextBtn).toBeTruthy();
});

test('renders Skip link on step 1', () => {
    global.localStorage.getItem.mockReturnValue(null);
    const vnode = OnboardingOverlay({});
    const buttons = findAll(vnode, n => n?.type === 'button');
    const skipBtn = buttons.find(b => collectText(b).some(t => t === 'Skip'));
    expect(skipBtn).toBeTruthy();
});

test('Skip onClick calls localStorage.setItem with oq_onboarding_done=1', () => {
    global.localStorage.getItem.mockReturnValue(null);
    const vnode = OnboardingOverlay({});
    const buttons = findAll(vnode, n => n?.type === 'button');
    const skipBtn = buttons.find(b => collectText(b).some(t => t === 'Skip'));
    expect(skipBtn).toBeTruthy();
    skipBtn.props.onClick();
    expect(global.localStorage.setItem).toHaveBeenCalledWith('oq_onboarding_done', '1');
});

test('renders Got it button on last step (step 5, index 4)', () => {
    global.localStorage.getItem.mockReturnValue(null);
    // First useSignal call = visible (true), second = step (4 = last step index)
    useSignal.mockReturnValueOnce({ value: true });
    useSignal.mockReturnValueOnce({ value: 4 });
    const vnode = OnboardingOverlay({});
    expect(vnode).not.toBeNull();
    const texts = collectText(vnode);
    expect(texts.some(t => t.includes('Got it'))).toBe(true);
    expect(texts.some(t => t === 'Next')).toBe(false);
});

test('handleNext onClick increments step value', () => {
    global.localStorage.getItem.mockReturnValue(null);
    const stepSig = { value: 0 };
    // First useSignal call = visible (true), second = step signal (mutable)
    useSignal.mockReturnValueOnce({ value: true });
    useSignal.mockReturnValueOnce(stepSig);
    const vnode = OnboardingOverlay({});
    const buttons = findAll(vnode, n => n?.type === 'button');
    const nextBtn = buttons.find(b => collectText(b).some(t => t === 'Next'));
    expect(nextBtn).toBeTruthy();
    nextBtn.props.onClick();
    expect(stepSig.value).toBe(1);
});
