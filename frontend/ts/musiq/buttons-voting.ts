import {keyOfElement} from './buttons';
import {state} from './update';
import {warningToastWithBar, errorToast} from '../base';
import {setStoredVote} from './vote-state';

/** Adds handlers to buttons that are visible when voting is enabled. */
export function onReady() {
  // Use a token bucket implementation to allow 10 Votes per minute.
  const maxTokens = 10;
  let currentTokens = maxTokens;
  const bucketLifetime = 30000; // half a minute
  let currentBucket = $.now();

  let lastActivationAt = 0;
  let lastActivationSignature = '';

  /** Makes sure that voting does not occur too often.
   * @return {boolean} whether voting is allowed. */
  function canVote() {
    const now = $.now();
    const timePassed = now - currentBucket;
    if (timePassed > bucketLifetime) {
      currentBucket = now;
      currentTokens = maxTokens - 1;
      return true;
    }

    if (currentTokens > 0) {
      currentTokens--;
      return true;
    }

    const ratio = (bucketLifetime - timePassed) / bucketLifetime;
    warningToastWithBar('You\'re doing that too often');
    $('#vote-timeout-bar').css('transition', 'none');
    $('#vote-timeout-bar').css('width', ratio * 100 + '%');
    $('#vote-timeout-bar')[0].offsetHeight;
    $('#vote-timeout-bar').css({
      'transition': 'width ' + ratio * bucketLifetime / 1000 + 's linear',
      'width': '0%',
    });
    return false;
  }

  /** Vote for a song.
   * @param {HTMLElement} button the button that was pressed to vote
   * @param {number} key the key of the voted song
   * @param {number} amount the amount of votes, from -2 to +2.
   * @param {?Function} onFail callback to restore the previous UI state. */
  function vote(button, key, amount, onFail = null) {
    let votes = button.closest('.queue-entry').find('.queue-vote-count');
    if (votes.length == 0) {
      votes = button.siblings('#current-song-votes');
    }

    const currentVotes = Number(votes.text()) || 0;
    votes.text(String(currentVotes + amount));

    $.post(urls['musiq']['vote'], {
      key: key,
      amount: amount,
    }).fail(function(response) {
      errorToast(response.responseText || 'Could not register vote');

      const failedVotes = Number(votes.text()) || 0;
      votes.text(String(failedVotes - amount));

      if (onFail) {
        onFail();
      }
    });
  }

  function triggerVoteAnimation(buttonElement) {
    if (!buttonElement || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      return;
    }

    buttonElement.classList.remove('furatic-vote-bump');
    // Trigger reflow so repeated taps restart the same animation.
    buttonElement.offsetWidth;
    buttonElement.classList.add('furatic-vote-bump');

    window.setTimeout(function() {
      buttonElement.classList.remove('furatic-vote-bump');
    }, 560);
  }

  function activationSignature(buttonElement) {
    const button = $(buttonElement).closest('.vote-up, .vote-down');
    const keyedElement = button.closest('[data-queue-key]');
    const key = keyedElement.attr('data-queue-key') || '';
    const id = button.attr('id') || '';
    const direction = button.hasClass('vote-up') ? 'up' : 'down';

    return direction + ':' + key + ':' + id;
  }

  function shouldIgnoreActivation(event, buttonElement) {
    const originalEvent = event.originalEvent || event;

    if (event.type === 'pointerdown') {
      if (
        originalEvent &&
        originalEvent.pointerType === 'mouse' &&
        originalEvent.button !== 0
      ) {
        return true;
      }

      if (
        originalEvent &&
        originalEvent.pointerType &&
        originalEvent.pointerType !== 'mouse'
      ) {
        event.preventDefault();
      }
    }

    if (event.type === 'touchstart') {
      event.preventDefault();
    }

    const now = Date.now();
    const signature = activationSignature(buttonElement);

    if (signature === lastActivationSignature && now - lastActivationAt < 450) {
      return true;
    }

    lastActivationSignature = signature;
    lastActivationAt = now;
    return false;
  }

  function resolveVoteKey(button) {
    if (button.closest('#current-song-card').length > 0) {
      if (state == null || state.currentSong == null) {
        return -1;
      }
      return state.currentSong.queueKey;
    }

    return keyOfElement(button);
  }

  function handleVotePress(buttonElement, direction) {
    const button = $(buttonElement);

    if (button.attr('data-furatic-own-vote-blocked') === 'true') {
      return;
    }

    const key = resolveVoteKey(button);
    if (key == -1) {
      return;
    }

    if (!canVote()) {
      return;
    }

    const up = direction === 'up' ? button : button.siblings('.vote-up');
    const down = direction === 'down' ? button : button.siblings('.vote-down');
    const previousState = up.hasClass('pressed') ? '+' : down.hasClass('pressed') ? '-' : '0';

    function applyVisualVoteState(value) {
      up.removeClass('pressed');
      down.removeClass('pressed');

      if (value === '+') {
        up.addClass('pressed');
      } else if (value === '-') {
        down.addClass('pressed');
      }

      setStoredVote(key, value);
    }

    function restorePreviousState() {
      applyVisualVoteState(previousState);
    }

    triggerVoteAnimation(buttonElement);

    if (direction === 'up') {
      if (up.hasClass('pressed')) {
        applyVisualVoteState('0');
        vote(button, key, -1, restorePreviousState);
      } else {
        applyVisualVoteState('+');
        vote(button, key, down.hasClass('pressed') ? 2 : 1, restorePreviousState);
      }
      return;
    }

    if (down.hasClass('pressed')) {
      applyVisualVoteState('0');
      vote(button, key, 1, restorePreviousState);
    } else {
      applyVisualVoteState('-');
      vote(button, key, up.hasClass('pressed') ? -2 : -1, restorePreviousState);
    }
  }

  function handleActivation(event) {
    const target = event.target instanceof Element ? event.target : null;
    const buttonElement = target ? target.closest('.vote-up, .vote-down') : null;

    if (!buttonElement) {
      return;
    }

    if (shouldIgnoreActivation(event, buttonElement)) {
      return;
    }

    if (buttonElement.classList.contains('vote-up')) {
      handleVotePress(buttonElement, 'up');
      return;
    }

    handleVotePress(buttonElement, 'down');
  }

  if (window.PointerEvent) {
    document.addEventListener('pointerdown', handleActivation, true);
  } else {
    document.addEventListener('touchstart', handleActivation, true);
    document.addEventListener('click', handleActivation, true);
  }
}

$(document).ready(() => {
  if (!["/musiq/", "/p/"].includes(window.location.pathname)) {
    return;
  }

  onReady();
});
