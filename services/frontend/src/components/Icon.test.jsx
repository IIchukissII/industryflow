// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, test, expect } from 'vitest';
import { render } from '@testing-library/react';
import Icon from './Icon';

describe('Icon', () => {
  test('renders an svg for a known icon name', () => {
    const { container } = render(<Icon name="check" size={20} />);
    const svg = container.querySelector('svg');
    expect(svg).toBeInTheDocument();
    expect(svg).toHaveAttribute('width', '20');
  });
  test('renders nothing for an unknown name', () => {
    const { container } = render(<Icon name="does-not-exist" />);
    expect(container.querySelector('svg')).toBeNull();
  });
  test('exposes a title as an accessible label', () => {
    const { getByRole } = render(<Icon name="alert" title="Warning" />);
    expect(getByRole('img', { name: 'Warning' })).toBeInTheDocument();
  });
});
