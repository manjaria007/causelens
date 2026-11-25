// TEST STUB for '@kirocrew/app-sdk/ui': plain elements with the same props, so the page's structure and text can be checked.
import { createElement } from 'react'

export const PageHeader = ({ title, subtitle }) => createElement('header', null, createElement('h1', null, title), createElement('p', null, subtitle))
export const Card = ({ children }) => createElement('section', { 'data-stub': 'Card' }, children)
export const CardTitle = ({ children }) => createElement('h2', null, children)
export const Btn = ({ children, onClick }) => createElement('button', { type: 'button', onClick }, children)
export const EmptyState = ({ title, subtitle }) => createElement('div', { 'data-stub': 'EmptyState' }, createElement('strong', null, title), createElement('p', null, subtitle))
